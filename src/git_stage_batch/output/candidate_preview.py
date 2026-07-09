"""Candidate preview output rendering."""

from __future__ import annotations

import json
import shlex

from ..batch.operation_candidates import (
    OperationCandidatePreview,
    render_candidate_buffer_diff,
)
from ..core.buffer import LineBuffer
from ..core.diff_parser import build_line_changes_from_patch_lines
from ..i18n import _
from ..utils.paths import get_context_lines
from . import candidate_preview_summary
from .colors import Colors


_CANDIDATE_OVERVIEW_MAX_CANDIDATES = 10


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


def _execute_candidate_command(preview: OperationCandidatePreview) -> str:
    return "git-stage-batch {command} --from {selector} --file {file}".format(
        command=preview.operation,
        selector=_candidate_selector_text(
            preview.batch_name,
            preview.operation,
            preview.ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )


def _candidate_choice_count(count: int) -> str:
    if count == 1:
        return _("1 choice")
    return _("{count} choices").format(count=count)


def _candidate_operation_past_tense(operation: str) -> str:
    if operation == "include":
        return _("included")
    return _("applied")


def _print_candidate_rule() -> None:
    rule = "─" * 78
    if Colors.enabled():
        print(f"{Colors.GRAY}{rule}{Colors.RESET}")
    else:
        print(rule)


def _print_candidate_header(status: str, *, note: str | None) -> None:
    if Colors.enabled():
        print(f"{Colors.BOLD}{status}{Colors.RESET}")
    else:
        print(status)
    if note:
        if Colors.enabled():
            print(f"{Colors.GRAY}{_('Note: {note}').format(note=note)}{Colors.RESET}")
        else:
            print(_("Note: {note}").format(note=note))
    _print_candidate_rule()


def _print_candidate_overview_header(
    first: OperationCandidatePreview,
    *,
    note: str | None,
) -> None:
    status = "  ·  ".join(
        (
            first.file_path,
            first.batch_name,
            _("{operation} candidates").format(operation=first.operation),
            _candidate_choice_count(first.count),
        )
    )
    _print_candidate_header(status, note=note)


def _print_candidate_detail_header(
    preview: OperationCandidatePreview,
    *,
    note: str | None,
) -> None:
    status = "  ·  ".join(
        (
            preview.file_path,
            preview.batch_name,
            _("{operation} candidate {ordinal}/{count}").format(
                operation=preview.operation,
                ordinal=preview.ordinal,
                count=preview.count,
            ),
        )
    )
    _print_candidate_header(status, note=note)


def _style_candidate_snippet_line(
    line: candidate_preview_summary.CandidateSnippetLine,
    *,
    width: int,
) -> str:
    plain = line.plain(width=width)
    if not Colors.enabled():
        return plain

    line_number = " " * width if line.line_number is None else f"{line.line_number:>{width}}"
    gutter = (
        f"{line_number}{candidate_preview_summary.CANDIDATE_GUTTER_SEPARATOR} "
    )
    body = (
        f"{line.marker}"
        f"{candidate_preview_summary.shorten_candidate_overview_text(line.text)}"
    )
    if line.highlight:
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.REVERSE}{Colors.GRAY}{body}{Colors.RESET}"
    if line.marker == "+":
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.GREEN}{body}{Colors.RESET}"
    if line.marker == "-":
        return f"{Colors.GRAY}{gutter}{Colors.RESET}{Colors.RED}{body}{Colors.RESET}"
    return f"{Colors.GRAY}{gutter}{Colors.RESET}{body}"


def _candidate_diff_hunks(diff_text: str) -> tuple[tuple[bytes, ...], ...]:
    headers: list[bytes] = []
    current_hunk: list[bytes] = []
    hunks: list[tuple[bytes, ...]] = []
    for line in diff_text.splitlines(keepends=True):
        line_bytes = line.encode("utf-8", errors="surrogateescape")
        if line_bytes.startswith((b"--- ", b"+++ ")):
            headers.append(line_bytes)
            continue
        if line_bytes.startswith(b"@@ "):
            if current_hunk:
                hunks.append(tuple(headers + current_hunk))
            current_hunk = [line_bytes]
            continue
        if current_hunk:
            current_hunk.append(line_bytes)

    if current_hunk:
        hunks.append(tuple(headers + current_hunk))
    return tuple(hunks)


def _candidate_line_number(line) -> int | None:
    if line.kind == "+":
        return line.new_line_number
    return line.old_line_number if line.old_line_number is not None else line.new_line_number


def _print_candidate_line_changes(
    line_changes,
    *,
    ambiguity_target_line_range: tuple[int, int] | None,
) -> None:
    use_color = Colors.enabled()
    header = line_changes.header
    header_part = f"@@ -{header.old_start},{header.old_len} +{header.new_start},{header.new_len} @@"
    if use_color:
        print(f"{Colors.BOLD}{line_changes.path}{Colors.RESET} :: {Colors.CYAN}{header_part}{Colors.RESET}")
    else:
        print(f"{line_changes.path} :: {header_part}")

    line_numbers = [
        line_number
        for line_number in (_candidate_line_number(line) for line in line_changes.lines)
        if line_number is not None
    ]
    width = max((len(str(line_number)) for line_number in line_numbers), default=1)

    for line in line_changes.lines:
        line_number = _candidate_line_number(line)
        gutter_number = " " * width if line_number is None else f"{line_number:>{width}}"
        gutter = (
            f"{gutter_number}"
            f"{candidate_preview_summary.CANDIDATE_GUTTER_SEPARATOR} "
        )
        body = f"{line.kind}{line.display_text()}"

        if not use_color:
            print(f"{gutter}{body}")
            continue

        styled_gutter = f"{Colors.GRAY}{gutter}{Colors.RESET}"
        if line.kind == "+":
            print(f"{styled_gutter}{Colors.GREEN}{body}{Colors.RESET}")
        elif line.kind == "-":
            print(f"{styled_gutter}{Colors.RED}{body}{Colors.RESET}")
        elif candidate_preview_summary.candidate_line_in_range(
            line_number,
            ambiguity_target_line_range,
        ):
            print(f"{styled_gutter}{Colors.REVERSE}{Colors.GRAY}{body}{Colors.RESET}")
        else:
            print(f"{styled_gutter}{body}")


def _print_candidate_buffer_diff(
    file_path: str,
    before_buffer: LineBuffer,
    after_buffer: LineBuffer,
    *,
    context_lines: int,
    ambiguity_target_line_range: tuple[int, int] | None,
    leading_blank: bool = False,
) -> bool:
    diff_text = render_candidate_buffer_diff(
        file_path,
        before_buffer,
        after_buffer,
        label_before="a",
        label_after="b",
        context_lines=context_lines,
    )
    if not diff_text:
        return False

    if leading_blank:
        print()

    hunks = _candidate_diff_hunks(diff_text)
    if not hunks:
        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
        return True

    for index, hunk in enumerate(hunks):
        if index:
            print()
        line_changes = build_line_changes_from_patch_lines(hunk)
        _print_candidate_line_changes(
            line_changes,
            ambiguity_target_line_range=ambiguity_target_line_range,
        )
    return True


def _print_candidate_summary_block(
    summary: candidate_preview_summary.CandidateTargetSummary,
    *,
    indent: str,
) -> None:
    width = candidate_preview_summary.snippet_line_width(summary.lines)
    for line in summary.lines:
        print(f"{indent}{_style_candidate_snippet_line(line, width=width)}")


def _print_common_candidate_target_blocks(
    previews: tuple[OperationCandidatePreview, ...],
    candidate_summaries: list[
        list[candidate_preview_summary.CandidateTargetSummary]
    ],
    common_target_indexes: tuple[int, ...],
) -> None:
    if not common_target_indexes:
        return

    first_summaries = candidate_summaries[0]
    for target_index in common_target_indexes:
        target = previews[0].targets[target_index]
        summary = first_summaries[target_index]
        label = candidate_preview_summary.candidate_target_label(target.target)
        print(
            _("{target} update, same for all candidates: {summary}").format(
                target=label,
                summary=summary.title,
            )
        )
        _print_candidate_summary_block(summary, indent="   ")
        print()


def render_operation_candidate_overview(
    previews: tuple[OperationCandidatePreview, ...],
    *,
    porcelain: bool,
    note: str | None = None,
) -> tuple[OperationCandidatePreview, ...]:
    first = previews[0]
    candidate_summaries = [
        [
            candidate_preview_summary.candidate_target_summary(target)
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
                            _execute_candidate_command(preview)
                        ),
                    },
                    "targets": [
                        {
                            "target": target.target,
                            "summary": summary.title,
                            "context": list(
                                candidate_preview_summary.plain_candidate_snippet_lines(
                                    summary.lines
                                )
                            ),
                        }
                        for target, summary in zip(preview.targets, summaries)
                    ],
                }
                for preview, summaries in zip(previews, candidate_summaries)
            ],
        }, sort_keys=True))
        return previews

    targets, verb = candidate_preview_summary.candidate_overview_subject(previews)
    _print_candidate_overview_header(
        first,
        note=note,
    )
    print(
        _("The {targets} {verb} changed in an ambiguous way since this batch was created.").format(
            targets=targets,
            verb=verb,
        )
    )
    print(
        _("The batch can be {operation} in more than one way:").format(
            operation=_candidate_operation_past_tense(first.operation),
        )
    )
    print()

    shown_previews = previews[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    shown_summaries = candidate_summaries[:_CANDIDATE_OVERVIEW_MAX_CANDIDATES]
    common_target_indexes = candidate_preview_summary.common_candidate_target_indexes(
        previews,
        candidate_summaries,
    )

    for preview, summaries in zip(shown_previews, shown_summaries):
        visible_summaries = [
            (target, summary)
            for target_index, (target, summary) in enumerate(zip(preview.targets, summaries))
            if target_index not in common_target_indexes
        ]
        candidate_header = _("Candidate {ordinal}/{count}").format(
            ordinal=preview.ordinal,
            count=preview.count,
        )
        if len(visible_summaries) == 1:
            _target, summary = visible_summaries[0]
            print(f"{candidate_header}   {summary.title}")
            _print_candidate_summary_block(summary, indent="   ")
        else:
            print(candidate_header)
            for target, summary in visible_summaries:
                label = candidate_preview_summary.candidate_target_label(target.target)
                print(f"   {label}: {summary.title}")
                _print_candidate_summary_block(summary, indent="      ")
        action = _("Apply") if preview.operation == "apply" else _("Include")
        print(f"   {_('Preview this candidate:')}")
        print(f"     {_show_candidate_command(preview, preview.ordinal)}")
        print(f"   {_('{action} this candidate:').format(action=action)}")
        print(f"     {_execute_candidate_command(preview)}")
        print()

    if len(previews) > len(shown_previews):
        remaining = len(previews) - len(shown_previews)
        print(
            _("... {count} more candidates. Preview another candidate with {selector}:N.").format(
                count=remaining,
                selector=_candidate_selector_text(first.batch_name, first.operation),
            )
        )
        print()
    _print_common_candidate_target_blocks(
        previews,
        candidate_summaries,
        common_target_indexes,
    )
    return tuple(shown_previews)


def render_operation_candidate(
    preview: OperationCandidatePreview,
    *,
    porcelain: bool,
    note: str | None,
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

    _print_candidate_detail_header(preview, note=note)

    context_lines = get_context_lines()
    for target_index, target in enumerate(preview.targets):
        summary = candidate_preview_summary.candidate_target_summary(target)
        has_multiple_targets = len(preview.targets) > 1
        if has_multiple_targets:
            if target_index > 0:
                print()
            target_label = candidate_preview_summary.candidate_target_label(
                target.target
            )
            print(
                _("{target} update: {summary}").format(
                    target=target_label,
                    summary=summary.title,
                ),
            )
        else:
            print(summary.title)
        _print_candidate_buffer_diff(
            preview.file_path,
            target.before_buffer,
            target.after_buffer,
            context_lines=context_lines,
            ambiguity_target_line_range=summary.ambiguity_line_range,
            leading_blank=not has_multiple_targets,
        )

    print()
    action = _("Apply") if preview.operation == "apply" else _("Include")
    print(
        _("{action} this candidate:\n  {command}").format(
            action=action,
            command=_execute_candidate_command(preview),
        ),
    )
    print()
    print(_("Review candidates:"))
    print(_("  overview: {command}").format(
        command=_show_candidate_command(preview),
    ))
    if preview.ordinal > 1:
        print(_("  previous: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal - 1),
        ))
    if preview.ordinal < preview.count:
        print(_("  next: {command}").format(
            command=_show_candidate_command(preview, preview.ordinal + 1),
        ))
