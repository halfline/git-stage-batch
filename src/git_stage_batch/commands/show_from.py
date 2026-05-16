"""Show from batch command implementation."""

from __future__ import annotations

import difflib
import json
import os
import shlex
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Optional

from ..batch.merge import MergeError
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operation_candidates import (
    OperationCandidatePreview,
    build_apply_candidate_previews,
    build_include_candidate_previews,
    render_candidate_buffer_diff,
    save_candidate_preview_state,
)
from ..batch.replacement import (
    ReplacementPayload,
    build_replacement_batch_view_from_lines,
    coerce_replacement_payload,
)
from ..batch.selection import (
    acquire_batch_ownership_for_display_ids_from_lines,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    translate_batch_file_gutter_ids_to_selection_ids,
)
from ..batch.source_selector import parse_batch_source_selector
from ..batch.submodule_pointer import is_batch_submodule_pointer
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    mode_for_text_materialization,
    normalized_text_change_type,
)
from ..data.hunk_tracking import (
    SelectedChangeKind,
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rendered_batch_file_display,
    clear_selected_change_state_files,
    compute_batch_binary_fingerprint,
    compute_batch_gitlink_fingerprint,
    mark_selected_change_cleared_by_file_list,
    render_batch_file_display,
)
from ..data.file_review_state import (
    FileReviewAction,
    ReviewSource,
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ..output import print_binary_file_change, print_gitlink_change, print_line_level_changes
from ..output.file_review import (
    build_file_review_model,
    make_file_review_state,
    normalize_page_spec,
    print_file_review,
    resolve_default_review_pages,
)
from ..output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    print_file_review_list,
)
from ..editor import (
    EditorBuffer,
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..exceptions import exit_with_error, BatchMetadataError, CommandError
from ..i18n import _
from ..core.models import BinaryFileChange, GitlinkChange, LineLevelChange
from ..utils.git import get_git_repository_root_path, require_git_repository
from ..utils.paths import get_context_lines


_CANDIDATE_OVERVIEW_CONTEXT_LINES = 1
_CANDIDATE_OVERVIEW_MAX_LINES = 5
_CANDIDATE_OVERVIEW_MAX_LINE_WIDTH = 64
_CANDIDATE_OVERVIEW_MAX_CANDIDATES = 10


def _batch_source_args(batch_name: str) -> str:
    return f" --from {shlex.quote(batch_name)}"


def _render_batch_binary_file_change(file_path: str, file_meta: dict) -> BinaryFileChange | None:
    """Return an atomic binary batch change for display, if the entry is binary."""
    if file_meta.get("file_type") != "binary":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return BinaryFileChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        change_type=change_type,
    )


def _render_batch_gitlink_change(file_path: str, file_meta: dict) -> GitlinkChange | None:
    """Return an atomic submodule pointer batch change, if the entry is one."""
    if file_meta.get("file_type") != "gitlink":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return GitlinkChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        old_oid=file_meta.get("old_oid"),
        new_oid=file_meta.get("new_oid"),
        change_type=change_type,
    )


def _shown_pages_for_display_ids(review_model, display_ids: set[int]) -> tuple[int, ...]:
    """Return review pages that contain the selected display IDs."""
    return tuple(
        sorted(
            {
                change.first_page
                for change in review_model.changes
                if set(change.display_ids) & display_ids
            }
        )
    )


def _require_contiguous_display_selection(selected_ids: set[int]) -> None:
    if not selected_ids:
        return
    selected_range = list(range(min(selected_ids), max(selected_ids) + 1))
    if sorted(selected_ids) != selected_range:
        exit_with_error(_("Replacement selection must be one contiguous line range."))


def _candidate_selector_text(
    batch_name: str,
    operation: str,
    ordinal: int | None = None,
) -> str:
    if ordinal is None:
        return f"{batch_name}:{operation}"
    return f"{batch_name}:{operation}:{ordinal}"


def _show_candidate_command(preview: OperationCandidatePreview, ordinal: int | None = None) -> str:
    return "git-stage-batch show --from {selector} --file {file}".format(
        selector=_candidate_selector_text(
            preview.batch_name,
            preview.operation,
            ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )


@dataclass(frozen=True)
class _CandidateTargetSummary:
    label: str
    title: str
    lines: tuple[str, ...]


def _decode_overview_lines(buffer: EditorBuffer) -> list[str]:
    text = buffer.to_bytes().decode("utf-8", errors="surrogateescape")
    return text.splitlines()


def _shorten_overview_text(text: str, max_width: int = _CANDIDATE_OVERVIEW_MAX_LINE_WIDTH) -> str:
    compact = text.strip()
    if len(compact) <= max_width:
        return compact
    if max_width <= 3:
        return compact[:max_width]
    return compact[: max_width - 3] + "..."


def _summarize_overview_lines(lines: list[str]) -> str:
    if not lines:
        return _("nothing")
    if len(lines) == 1:
        text = _shorten_overview_text(lines[0], 36)
        if text:
            return f'"{text}"'
        return _("an empty line")
    return _("{count} lines").format(count=len(lines))


def _first_changed_opcode(
    before_lines: list[str],
    after_lines: list[str],
) -> tuple[str, int, int, int, int] | None:
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag != "equal":
            return tag, before_start, before_end, after_start, after_end
    return None


def _nearby_context_summary(
    before_lines: list[str],
    before_start: int,
    before_end: int,
    changed_lines: list[str],
) -> str:
    changed_text = {line.strip() for line in changed_lines if line.strip()}
    candidates = []
    if before_start > 0:
        candidates.append(before_lines[before_start - 1])
    if before_end < len(before_lines):
        candidates.append(before_lines[before_end])

    for line in candidates:
        text = _shorten_overview_text(line, 36)
        if text and text not in changed_text:
            return _(' near "{context}"').format(context=text)

    for line in candidates:
        text = _shorten_overview_text(line, 36)
        if text:
            return _(' near "{context}"').format(context=text)
    return ""


def _overview_action_title(
    tag: str,
    before_lines: list[str],
    after_lines: list[str],
    before_start: int,
    before_end: int,
    after_start: int,
    after_end: int,
) -> str:
    removed = before_lines[before_start:before_end]
    added = after_lines[after_start:after_end]
    changed = removed or added
    nearby = _nearby_context_summary(before_lines, before_start, before_end, changed)

    if tag == "delete":
        return _("Remove {text}{nearby}").format(
            text=_summarize_overview_lines(removed),
            nearby=nearby,
        )
    if tag == "insert":
        return _("Add {text}{nearby}").format(
            text=_summarize_overview_lines(added),
            nearby=nearby,
        )
    return _("Replace {old} with {new}{nearby}").format(
        old=_summarize_overview_lines(removed),
        new=_summarize_overview_lines(added),
        nearby=nearby,
    )


def _append_overview_line(
    lines: list[str],
    *,
    line_number: int,
    width: int,
    marker: str,
    text: str,
) -> None:
    lines.append(
        f"{line_number:>{width}} {marker}{_shorten_overview_text(text)}"
    )


def _overview_snippet_lines(
    before_lines: list[str],
    after_lines: list[str],
    before_start: int,
    before_end: int,
    after_start: int,
    after_end: int,
) -> tuple[str, ...]:
    width = max(len(str(max(len(before_lines), len(after_lines), 1))), 1)
    lines: list[str] = []

    context_start = max(0, before_start - _CANDIDATE_OVERVIEW_CONTEXT_LINES)
    context_end = min(len(before_lines), before_end + _CANDIDATE_OVERVIEW_CONTEXT_LINES)

    for index in range(context_start, before_start):
        _append_overview_line(
            lines,
            line_number=index + 1,
            width=width,
            marker=" ",
            text=before_lines[index],
        )

    for index in range(before_start, before_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            width=width,
            marker="-",
            text=before_lines[index],
        )

    for index in range(after_start, after_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            width=width,
            marker="+",
            text=after_lines[index],
        )

    for index in range(before_end, context_end):
        _append_overview_line(
            lines,
            line_number=index + 1,
            width=width,
            marker=" ",
            text=before_lines[index],
        )

    if len(lines) > _CANDIDATE_OVERVIEW_MAX_LINES:
        return tuple(lines[:_CANDIDATE_OVERVIEW_MAX_LINES] + ["..."])
    return tuple(lines)


def _summarize_candidate_target(target) -> _CandidateTargetSummary:
    before_lines = _decode_overview_lines(target.before_buffer)
    after_lines = _decode_overview_lines(target.after_buffer)
    opcode = _first_changed_opcode(before_lines, after_lines)
    label = _("Index") if target.target == "index" else _("Working tree")
    if opcode is None:
        return _CandidateTargetSummary(label=label, title=_("No text changes"), lines=())

    tag, before_start, before_end, after_start, after_end = opcode
    return _CandidateTargetSummary(
        label=label,
        title=_overview_action_title(
            tag,
            before_lines,
            after_lines,
            before_start,
            before_end,
            after_start,
            after_end,
        ),
        lines=_overview_snippet_lines(
            before_lines,
            after_lines,
            before_start,
            before_end,
            after_start,
            after_end,
        ),
    )


def _render_operation_candidate_overview(
    previews: tuple[OperationCandidatePreview, ...],
    *,
    porcelain: bool,
) -> tuple[OperationCandidatePreview, ...]:
    first = previews[0]
    candidate_summaries = [
        [
            _summarize_candidate_target(target)
            for target in preview.targets
        ]
        for preview in previews
    ]

    if porcelain:
        print(json.dumps({
            "status": "candidates",
            "changed": False,
            "batch": first.batch_name,
            "selector": {
                "operation": first.operation,
                "count": len(previews),
            },
            "scope": {
                "file": first.file_path,
            },
            "candidates": [
                {
                    "ordinal": preview.ordinal,
                    "selector": _candidate_selector_text(
                        preview.batch_name,
                        preview.operation,
                        preview.ordinal,
                    ),
                    "commands": {
                        "preview": (
                            "git-stage-batch show --from {selector} --file {file}".format(
                                selector=_candidate_selector_text(
                                    preview.batch_name,
                                    preview.operation,
                                    preview.ordinal,
                                ),
                                file=shlex.quote(preview.file_path),
                            )
                        ),
                        "execute": (
                            "git-stage-batch {command} --from {selector} --file {file}".format(
                                command=preview.operation,
                                selector=_candidate_selector_text(
                                    preview.batch_name,
                                    preview.operation,
                                    preview.ordinal,
                                ),
                                file=shlex.quote(preview.file_path),
                            )
                        ),
                    },
                    "targets": [
                        {
                            "target": target.target,
                            "summary": summary.title,
                            "context": list(summary.lines),
                        }
                        for target, summary in zip(preview.targets, summaries)
                    ],
                }
                for preview, summaries in zip(previews, candidate_summaries)
            ],
        }, sort_keys=True))
        return previews

    operation = first.operation.capitalize()
    print(
        _("{operation} candidates for batch '{batch}' in {file}.").format(
            operation=operation,
            batch=first.batch_name,
            file=first.file_path,
        )
    )
    print(_("Candidates: {count}").format(count=len(previews)))
    print(_("No changes applied."))
    print()

    shown_previews = previews[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    shown_summaries = candidate_summaries[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    for preview, summaries in zip(shown_previews, shown_summaries):
        if len(summaries) == 1:
            summary = summaries[0]
            print(f"{preview.ordinal}. {summary.label}: {summary.title}")
            for line in summary.lines:
                print(f"   {line}")
        else:
            print(f"{preview.ordinal}.")
            for summary in summaries:
                print(f"   {summary.label}: {summary.title}")
                for line in summary.lines:
                    print(f"      {line}")
        print(
            "   show: git-stage-batch show --from {selector} --file {file}".format(
                selector=_candidate_selector_text(
                    preview.batch_name,
                    preview.operation,
                    preview.ordinal,
                ),
                file=shlex.quote(preview.file_path),
            )
        )
        print(
            "   {command}: git-stage-batch {command} --from {selector} --file {file}".format(
                command=preview.operation,
                selector=_candidate_selector_text(
                    preview.batch_name,
                    preview.operation,
                    preview.ordinal,
                ),
                file=shlex.quote(preview.file_path),
            )
        )
        print()

    if len(previews) > len(shown_previews):
        remaining = len(previews) - len(shown_previews)
        print(
            _("... {count} more candidates. Preview another candidate with {selector}:N.").format(
                count=remaining,
                selector=_candidate_selector_text(first.batch_name, first.operation),
            )
        )
    return tuple(shown_previews)


def _render_operation_candidate(
    preview: OperationCandidatePreview,
    *,
    porcelain: bool,
) -> None:
    if porcelain:
        print(json.dumps({
            "status": "candidate",
            "changed": False,
            "batch": preview.batch_name,
            "selector": {
                "operation": preview.operation,
                "ordinal": preview.ordinal,
                "count": preview.count,
                "id": preview.candidate_id,
            },
            "scope": {
                "file": preview.file_path,
            },
            "targets": [
                {
                    "target": target.target,
                    "file": target.file_path,
                    "summary": target.summary,
                    "resolution_ordinal": target.resolution_ordinal,
                    "resolution_count": target.resolution_count,
                }
                for target in preview.targets
            ],
        }, sort_keys=True))
        return

    print(
        _("{operation} candidate {ordinal} of {count} for batch '{batch}'.").format(
            operation=preview.operation.capitalize(),
            ordinal=preview.ordinal,
            count=preview.count,
            batch=preview.batch_name,
        ),
        file=sys.stderr,
    )
    print(_("No changes applied."), file=sys.stderr)
    print("", file=sys.stderr)
    print(preview.file_path, file=sys.stderr)
    print(
        _("Candidate {ordinal} of {count}").format(
            ordinal=preview.ordinal,
            count=preview.count,
        ),
        file=sys.stderr,
    )

    context_lines = get_context_lines()
    for target in preview.targets:
        if len(preview.targets) > 1:
            print(
                _("{target} result:").format(
                    target="Index" if target.target == "index" else "Working-tree"
                ),
                file=sys.stderr,
            )
        if target.summary:
            print(_("Plan: {summary}").format(summary=target.summary), file=sys.stderr)
        if target.explanation:
            print(_("Reason: {reason}").format(reason=target.explanation), file=sys.stderr)
        diff_text = render_candidate_buffer_diff(
            preview.file_path,
            target.before_buffer,
            target.after_buffer,
            label_before=target.target,
            label_after=f"{target.target}-candidate",
            context_lines=context_lines,
        )
        if diff_text:
            print(diff_text, end="" if diff_text.endswith("\n") else "\n")

    print("", file=sys.stderr)
    execute_command = (
        "apply" if preview.operation == "apply" else "include"
    )
    action = _("Apply") if preview.operation == "apply" else _("Include")
    print(
        _("{action} this candidate:\n  git-stage-batch {command} --from {selector} --file {file}").format(
            action=action,
            command=execute_command,
            selector=_candidate_selector_text(
                preview.batch_name,
                preview.operation,
                preview.ordinal,
            ),
            file=shlex.quote(preview.file_path),
        ),
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print(_("Review candidates:"), file=sys.stderr)
    print(_("  overview: {command}").format(
        command=_show_candidate_command(preview),
    ), file=sys.stderr)
    if preview.ordinal > 1:
        print(_("  previous: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal - 1),
        ), file=sys.stderr)
    if preview.ordinal < preview.count:
        print(_("  next: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal + 1),
        ), file=sys.stderr)


def _resolve_candidate_ordinal(
    previews: tuple[OperationCandidatePreview, ...],
    *,
    explicit_ordinal: int,
) -> OperationCandidatePreview:
    if not previews:
        raise CommandError(_("No candidates available."))
    first = previews[0]
    ordinal = explicit_ordinal

    if ordinal > len(previews):
        raise CommandError(
            _("Batch '{batch}' has {count} {operation} candidates for {file}; candidate {ordinal} does not exist.").format(
                batch=first.batch_name,
                count=len(previews),
                operation=first.operation,
                file=first.file_path,
                ordinal=ordinal,
            )
        )
    if ordinal < 1:
        raise CommandError(_("Candidate ordinal must be at least 1."))
    return previews[ordinal - 1]


def _preview_replacement_batch_view(
    batch_name: str,
    metadata: dict,
    files: dict,
    line_ids: str,
    file_path: str,
    selected_ids: set[int],
    replacement_text: str | ReplacementPayload,
) -> None:
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary":
        exit_with_error(_("Cannot preview replacement text for binary files."))
    if is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Cannot preview replacement text for submodule pointers."))

    _require_contiguous_display_selection(selected_ids)
    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    with batch_source_buffer as batch_source_lines:
        selection_ids, _rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path,
            selected_ids,
            # Replacement preview is include-shaped because it previews include --from --as.
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids,
        ) as ownership:
            try:
                replacement_view = build_replacement_batch_view_from_lines(
                    batch_source_lines,
                    ownership,
                    coerce_replacement_payload(replacement_text),
                )
            except ValueError as e:
                exit_with_error(str(e))
            with replacement_view:
                before = EditorBuffer.from_bytes(batch_source_buffer.to_bytes())
                try:
                    diff_text = render_candidate_buffer_diff(
                        file_path,
                        before,
                        replacement_view.source_buffer,
                        label_before="batch",
                        label_after="replacement-preview",
                        context_lines=get_context_lines(),
                    )
                    if diff_text:
                        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
                finally:
                    before.close()


def _build_candidate_previews(
    *,
    selector,
    metadata: dict,
    files: dict,
    file_path: str,
    selected_ids: set[int] | None,
    replacement_text: str | ReplacementPayload | None,
) -> tuple[OperationCandidatePreview, ...]:
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Candidate preview is only available for text batch entries."))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))

    with batch_source_buffer as batch_source_lines:
        selection_ids_to_apply = selected_ids
        if selected_ids:
            action = (
                FileReviewAction.APPLY_FROM_BATCH
                if selector.candidate_operation == "apply"
                else FileReviewAction.INCLUDE_FROM_BATCH
            )
            selection_ids_to_apply, _rendered = translate_batch_file_gutter_ids_to_selection_ids(
                selector.batch_name,
                file_path,
                selected_ids,
                action,
            )

        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            with ExitStack() as stack:
                source_for_candidates = batch_source_lines
                candidate_ownership = ownership
                replacement_payload = None
                if replacement_text is not None:
                    if selector.candidate_operation == "apply":
                        exit_with_error(_("Replacement preview is not valid for apply candidates."))
                    if not selected_ids:
                        exit_with_error(_("`show --from --as` requires `--line`."))
                    _require_contiguous_display_selection(selected_ids)
                    replacement_payload = coerce_replacement_payload(replacement_text)
                    try:
                        replacement_view = build_replacement_batch_view_from_lines(
                            batch_source_lines,
                            ownership,
                            replacement_payload,
                        )
                    except ValueError as e:
                        exit_with_error(str(e))
                    replacement_view = stack.enter_context(replacement_view)
                    source_for_candidates = replacement_view.source_buffer
                    candidate_ownership = replacement_view.ownership

                if selector.candidate_operation == "apply":
                    worktree_file_mode = mode_for_text_materialization(
                        batch_file_mode,
                        selected_ids,
                        destination_exists=working_exists,
                    )
                    with load_working_tree_file_as_buffer(file_path) as working_lines:
                        return build_apply_candidate_previews(
                            batch_name=selector.batch_name,
                            file_path=file_path,
                            source_lines=source_for_candidates,
                            ownership=candidate_ownership,
                            worktree_lines=working_lines,
                            batch_source_commit=batch_source_commit,
                            file_meta=file_meta,
                            text_change_type=text_change_type,
                            worktree_file_mode=worktree_file_mode,
                            worktree_exists=working_exists,
                            selected_ids=selected_ids,
                            selection_ids=selection_ids_to_apply,
                        )

                index_buffer = load_git_object_as_buffer(f":{file_path}")
                index_exists = index_buffer is not None
                if index_buffer is None:
                    index_buffer = EditorBuffer.from_bytes(b"")
                index_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=index_exists,
                )
                worktree_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=working_exists,
                )
                with (
                    index_buffer as index_lines,
                    load_working_tree_file_as_buffer(file_path) as working_lines,
                ):
                    return build_include_candidate_previews(
                        batch_name=selector.batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=worktree_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
                        selected_ids=selected_ids,
                        selection_ids=selection_ids_to_apply,
                        replacement_payload=replacement_payload,
                    )


def command_show_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    selectable: bool = True,
    page: str | None = None,
    porcelain: bool = False,
    replacement_text: str | ReplacementPayload | None = None,
) -> None:
    """Show changes from a batch.

    Args:
        batch_name: Name of batch to show
        line_ids: Optional line IDs to filter (requires single-file context)
        file: Optional file path to show from batch.
              If None, shows all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        selectable: If True, cache the displayed file for later line operations.
        page: Optional file-review page selection.
    """
    require_git_repository()
    selector = parse_batch_source_selector(batch_name)
    batch_name = selector.batch_name

    if selector.candidate_operation is not None and page is not None:
        exit_with_error(_("Candidate preview does not support --page."))

    # Check if batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    # Resolve file scope (for consistent --file handling across commands)
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "show"
    )

    if selector.candidate_operation is not None:
        if patterns is not None or len(files) != 1:
            exit_with_error(_("Candidate preview requires exactly one file."))
        file_path = list(files.keys())[0]
        try:
            previews = _build_candidate_previews(
                selector=selector,
                metadata=metadata,
                files=files,
                file_path=file_path,
                selected_ids=selected_ids,
                replacement_text=replacement_text,
            )
        except ValueError as e:
            exit_with_error(str(e))
        except MergeError as e:
            exit_with_error(str(e))

        if not previews:
            exit_with_error(
                _("Batch '{batch}' has no {operation} candidates for {file}.").format(
                    batch=batch_name,
                    operation=selector.candidate_operation,
                    file=file_path,
                )
            )

        if selector.candidate_ordinal is None:
            try:
                reviewed_previews = _render_operation_candidate_overview(
                    previews,
                    porcelain=porcelain,
                )
                for preview in reviewed_previews:
                    save_candidate_preview_state(preview)
            finally:
                for candidate in previews:
                    candidate.close()
            return

        preview = _resolve_candidate_ordinal(previews, explicit_ordinal=selector.candidate_ordinal)
        try:
            _render_operation_candidate(
                preview,
                porcelain=porcelain,
            )
            save_candidate_preview_state(preview)
        finally:
            for candidate in previews:
                candidate.close()
        return

    if porcelain:
        exit_with_error(_("--porcelain is only supported for candidate preview in `show --from`."))
    if replacement_text is not None:
        if not line_ids:
            exit_with_error(_("`show --from --as` requires `--line`."))
        if len(files) != 1:
            exit_with_error(_("`show --from --as` requires exactly one file."))
        file_path = list(files.keys())[0]
        _preview_replacement_batch_view(
            batch_name,
            metadata,
            files,
            line_ids,
            file_path,
            selected_ids,
            replacement_text,
        )
        return

    if len(files) == 1:
        # Show specific file from batch
        # Get the resolved file path
        file_path = list(files.keys())[0]
        binary_change = _render_batch_binary_file_change(file_path, files[file_path])
        if binary_change is not None:
            if selected_ids:
                exit_with_error(
                    _("Cannot use --lines with binary files. Run without --lines to view the binary change summary.")
                )
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_selected_change_state_files()
                cache_binary_file_change(
                    binary_change,
                    kind=SelectedChangeKind.BATCH_BINARY,
                    batch_name=batch_name,
                    batch_binary_fingerprint=compute_batch_binary_fingerprint(
                        batch_name,
                        file_path,
                        files[file_path],
                    ),
                )
            print_binary_file_change(binary_change)
            return

        gitlink_change = _render_batch_gitlink_change(file_path, files[file_path])
        if gitlink_change is not None:
            if selected_ids:
                exit_with_error(
                    _("Cannot use --lines with submodule pointers. Run without --lines to view the submodule pointer summary.")
                )
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_selected_change_state_files()
                cache_gitlink_change(
                    gitlink_change,
                    kind=SelectedChangeKind.BATCH_GITLINK,
                    batch_name=batch_name,
                    batch_gitlink_fingerprint=compute_batch_gitlink_fingerprint(
                        file_path,
                        files[file_path],
                    ),
                )
            print_gitlink_change(gitlink_change)
            return

        rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
        if rendered is None:
            print(_("No changes for file '{file}' in batch '{name}'.").format(file=file_path, name=batch_name), file=sys.stderr)
            return

        review_model = None
        review_gutter_to_selection_id = (
            rendered.review_gutter_to_selection_id
            or rendered.gutter_to_selection_id
        )
        review_selection_id_to_gutter = (
            rendered.review_selection_id_to_gutter
            or rendered.selection_id_to_gutter
        )
        review_action_groups = rendered.review_action_groups or None

        def get_review_model():
            nonlocal review_model
            if review_model is None:
                review_model = build_file_review_model(
                    rendered.line_changes,
                    gutter_to_selection_id=review_gutter_to_selection_id,
                    actionable_selection_groups=rendered.actionable_selection_groups,
                    review_action_groups=review_action_groups,
                )
            return review_model

        if selectable and page is not None:
            resolve_default_review_pages(
                get_review_model(),
                requested_page_spec=page,
                previous_selection=None,
            )

        if page is not None or (selectable and not selected_ids):
            review_model = get_review_model()
            shown_pages = resolve_default_review_pages(
                review_model,
                requested_page_spec=page,
                previous_selection=None,
            )
            page_spec = normalize_page_spec(shown_pages, len(review_model.pages))
            if selectable:
                clear_last_file_review_state()
                cache_rendered_batch_file_display(file_path, rendered)
                write_last_file_review_state(
                    make_file_review_state(
                        review_model,
                        source=ReviewSource.BATCH,
                        batch_name=batch_name,
                        shown_pages=shown_pages,
                        selected_change_kind=SelectedChangeKind.BATCH_FILE,
                        gutter_to_selection_id=review_gutter_to_selection_id,
                        actionable_selection_groups=rendered.actionable_selection_groups,
                        review_action_groups=review_action_groups,
                    )
                )
            print_file_review(
                review_model,
                shown_pages=shown_pages,
                source_label=_("Changes: batch {name}").format(name=batch_name),
                page_spec=page_spec,
                command_source_args=_batch_source_args(batch_name),
                source=ReviewSource.BATCH,
                batch_name=batch_name,
                note=metadata.get("note") or None,
            )
            return

        # Filter by line IDs if specified (for display only)
        if selected_ids:
            line_gutter_to_selection_id = (
                review_gutter_to_selection_id
                if selectable else
                rendered.gutter_to_selection_id
            )

            # Translate gutter IDs (what user sees) to selection IDs (internal)
            selection_ids = set()
            for gutter_id in selected_ids:
                if gutter_id in line_gutter_to_selection_id:
                    selection_ids.add(line_gutter_to_selection_id[gutter_id])
                else:
                    exit_with_error(
                        _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                            id=gutter_id
                        )
                    )

            if selectable:
                clear_last_file_review_state()
                cache_rendered_batch_file_display(file_path, rendered)
                review_model = get_review_model()
                visible_review_display_ids = {
                    review_selection_id_to_gutter[selection_id]
                    for selection_id in selection_ids
                    if selection_id in review_selection_id_to_gutter
                }
                shown_pages = _shown_pages_for_display_ids(review_model, visible_review_display_ids)
                if shown_pages:
                    write_last_file_review_state(
                        make_file_review_state(
                            review_model,
                            source=ReviewSource.BATCH,
                            batch_name=batch_name,
                            shown_pages=shown_pages,
                            selected_change_kind=SelectedChangeKind.BATCH_FILE,
                            gutter_to_selection_id=review_gutter_to_selection_id,
                            actionable_selection_groups=rendered.actionable_selection_groups,
                            review_action_groups=review_action_groups,
                            visible_display_ids=visible_review_display_ids,
                            entire_file_shown=False,
                        )
                    )

            # Filter by selection IDs (not gutter IDs)
            filtered_lines = [line for line in rendered.line_changes.lines if line.id in selection_ids]
            if filtered_lines:
                filtered_line_changes = LineLevelChange(
                    path=rendered.line_changes.path,
                    lines=filtered_lines,
                    header=rendered.line_changes.header
                )
                print_line_level_changes(filtered_line_changes, gutter_to_selection_id=line_gutter_to_selection_id)
        else:
            print_line_level_changes(
                    rendered.line_changes,
                    gutter_to_selection_id=(
                        review_gutter_to_selection_id
                        if selectable else
                        {}
                    ),
                )

        return

    entries = []
    for file_path, file_meta in files.items():
        binary_change = _render_batch_binary_file_change(file_path, file_meta)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = _render_batch_gitlink_change(file_path, file_meta)
        if gitlink_change is not None:
            entries.append(make_gitlink_file_review_list_entry(gitlink_change))
            continue
        rendered = render_batch_file_display(
            batch_name,
            file_path,
            metadata=metadata,
            probe_mergeability=False,
        )
        if rendered is not None:
            entries.append(
                make_file_review_list_entry(
                    rendered.line_changes,
                )
            )

    if entries:
        # Multi-file batch output is navigational; it must not leave a hidden
        # selected file that a later bare action could operate on.
        if selectable:
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_file_list(
                source=ReviewSource.BATCH.value,
                batch_name=batch_name,
            )
        print_file_review_list(
            source_label=_("Changes: batch {name}").format(name=batch_name),
            entries=entries,
            command_source_args=_batch_source_args(batch_name),
        )
    else:
        print(_("Batch '{name}' is empty").format(name=batch_name), file=sys.stderr)
