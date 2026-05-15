"""Status command implementation."""

from __future__ import annotations

import json
from pathlib import Path
from string import Formatter
import subprocess
import sys

from ..batch.query import read_batch_metadata
from ..core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_stable_hunk_hash_from_lines,
)
from ..core.diff_parser import acquire_unified_diff
from ..core.models import BinaryFileChange, GitlinkChange
from ..data.file_review_state import (
    FileReviewAction,
    ReviewSource,
    read_last_file_review_state,
    selected_change_matches_review_state,
    shown_review_selections_for_action,
)
from ..data.hunk_tracking import (
    SelectedChangeKind,
    binary_file_change_is_stale,
    clear_selected_change_state_files,
    format_id_range,
    gitlink_change_is_stale,
    load_selected_binary_file,
    load_selected_gitlink_change,
    mark_selected_change_cleared_by_stale_batch_selection,
    read_selected_change_kind,
    selected_batch_binary_batch_name,
    selected_batch_binary_file_for_batch,
    snapshots_are_stale,
)
from ..data.line_state import load_line_changes_from_state
from ..data.session import get_iteration_count
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import (
    count_nonblank_text_file_lines,
    stream_text_file_lines,
    read_file_paths_file,
    read_text_file_line_set,
)
from ..utils.git import require_git_repository, run_git_command, stream_git_diff
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
    get_state_directory_path,
)


DEFAULT_PROMPT_FORMAT = "STAGING"
_PROMPT_FIELDS = frozenset(
    {
        "active",
        "change_type",
        "discarded",
        "file_review_batch",
        "file_review_fresh",
        "file_review_source",
        "included",
        "in_progress",
        "iteration",
        "processed",
        "progress_label",
        "progress_status",
        "remaining",
        "selected_file",
        "selected_ids",
        "selected_kind",
        "selected_line",
        "skipped",
        "status",
        "status_label",
        "total",
    }
)
_LIGHT_PROMPT_FIELDS = frozenset({"active"})


def estimate_remaining_hunks() -> int:
    """Estimate number of remaining unprocessed hunks.

    Returns:
        Number of hunks not yet included, skipped, or discarded
    """
    # Filter out blocked hunks
    blocked_hashes = read_text_file_line_set(get_block_list_file_path())

    # Filter out hunks from blocked files
    blocked_files = read_file_paths_file(get_blocked_files_file_path())

    remaining = 0
    try:
        with acquire_unified_diff(
            stream_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, GitlinkChange):
                    hunk_hash = compute_gitlink_change_hash(patch)
                    file_path = patch.path()
                elif isinstance(patch, BinaryFileChange):
                    hunk_hash = compute_binary_file_hash(patch)
                    file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                else:
                    hunk_hash = compute_stable_hunk_hash_from_lines(patch.lines)
                    file_path = patch.old_path if patch.old_path != "/dev/null" else patch.new_path
                file_path = file_path.removeprefix("a/").removeprefix("b/")

                if hunk_hash not in blocked_hashes and file_path not in blocked_files:
                    remaining += 1
    except Exception:
        return 0

    return remaining


def _selected_change_is_stale(selected_kind: SelectedChangeKind | None, file_path: str) -> bool:
    """Return whether selected state should be treated as stale by status."""
    if selected_kind in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY):
        return False
    return snapshots_are_stale(file_path)


def _selected_kind_label(selected_kind: str | None) -> str:
    labels = {
        SelectedChangeKind.HUNK.value: _("Current hunk:"),
        SelectedChangeKind.FILE.value: _("Current file review:"),
        SelectedChangeKind.BATCH_FILE.value: _("Current batch file review:"),
        SelectedChangeKind.BINARY.value: _("Current binary file:"),
        SelectedChangeKind.BATCH_BINARY.value: _("Current batch binary file:"),
        SelectedChangeKind.GITLINK.value: _("Current submodule pointer:"),
        SelectedChangeKind.BATCH_GITLINK.value: _("Current batch submodule pointer:"),
    }
    return labels.get(selected_kind or SelectedChangeKind.HUNK.value, _("Current selection:"))


def _read_batch_review_display_ids(file_path: str) -> list[int]:
    """Return user-visible gutter IDs for the current batch file review."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return []
    if review_state.source != ReviewSource.BATCH or review_state.file_path != file_path:
        return []
    try:
        if not selected_change_matches_review_state(review_state):
            return []
    except Exception:
        return []

    return sorted({
        display_id
        for selection in shown_review_selections_for_action(
            review_state,
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
        for display_id in selection.display_ids
    })


def _read_live_review_display_ids(file_path: str) -> list[int] | None:
    """Return shown live-review gutter IDs.

    None means no matching live review exists; an empty list can also mean a
    matching review exists but is no longer fresh.
    """
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if review_state.source != ReviewSource.FILE_VS_HEAD or review_state.file_path != file_path:
        return None
    try:
        if not selected_change_matches_review_state(review_state):
            return []
    except Exception:
        return []

    return sorted({
        display_id
        for selection in shown_review_selections_for_action(
            review_state,
            FileReviewAction.INCLUDE,
        )
        for display_id in selection.display_ids
    })


def _read_selected_change_summary() -> tuple[bool, dict | None]:
    """Return whether a non-stale selected change exists and its status summary."""
    selected_kind = read_selected_change_kind()
    if selected_kind in (SelectedChangeKind.GITLINK, SelectedChangeKind.BATCH_GITLINK):
        gitlink_change = load_selected_gitlink_change()
        if gitlink_change is None:
            return False, None
        if selected_kind == SelectedChangeKind.GITLINK and gitlink_change_is_stale(gitlink_change):
            return False, None
        return True, {
            "kind": selected_kind.value,
            "file": gitlink_change.path(),
            "line": None,
            "ids": [],
            "change_type": gitlink_change.change_type,
            "old_oid": gitlink_change.old_oid,
            "new_oid": gitlink_change.new_oid,
        }

    if selected_kind in (SelectedChangeKind.BINARY, SelectedChangeKind.BATCH_BINARY):
        binary_file = load_selected_binary_file()
        if binary_file is None:
            return False, None
        if selected_kind == SelectedChangeKind.BINARY and binary_file_change_is_stale(binary_file):
            return False, None
        file_path = binary_file.new_path if binary_file.new_path != "/dev/null" else binary_file.old_path
        if selected_kind == SelectedChangeKind.BATCH_BINARY:
            batch_name = selected_batch_binary_batch_name()
            if batch_name is None:
                clear_selected_change_state_files()
                return False, None
            metadata = read_batch_metadata(batch_name)
            if selected_batch_binary_file_for_batch(batch_name, metadata.get("files", {})) is None:
                clear_selected_change_state_files()
                mark_selected_change_cleared_by_stale_batch_selection(
                    batch_name=batch_name,
                    file_path=file_path,
                )
                return False, None
        return True, {
            "kind": selected_kind.value,
            "file": file_path,
            "line": None,
            "ids": [],
            "change_type": binary_file.change_type,
        }

    if not get_selected_hunk_patch_file_path().exists() or not get_line_changes_json_file_path().exists():
        return False, None

    try:
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            return False, None
        if selected_kind == SelectedChangeKind.BATCH_FILE:
            review_state = read_last_file_review_state()
            if review_state is not None:
                try:
                    if not selected_change_matches_review_state(review_state):
                        if review_state.source == ReviewSource.BATCH and review_state.batch_name is not None:
                            mark_batch_name = review_state.batch_name
                            mark_file_path = review_state.file_path
                        else:
                            mark_batch_name = None
                            mark_file_path = None
                        clear_selected_change_state_files()
                        if mark_batch_name is not None and mark_file_path is not None:
                            mark_selected_change_cleared_by_stale_batch_selection(
                                batch_name=mark_batch_name,
                                file_path=mark_file_path,
                            )
                        return False, None
                except Exception:
                    clear_selected_change_state_files()
                    return False, None
        if _selected_change_is_stale(selected_kind, line_changes.path):
            return False, None
        kind_value = selected_kind.value if selected_kind is not None else SelectedChangeKind.HUNK.value
        if selected_kind == SelectedChangeKind.BATCH_FILE:
            ids = _read_batch_review_display_ids(line_changes.path)
        elif selected_kind == SelectedChangeKind.FILE:
            ids = _read_live_review_display_ids(line_changes.path)
            if ids is None:
                ids = line_changes.changed_line_ids()
        else:
            ids = line_changes.changed_line_ids()
        return True, {
            "kind": kind_value,
            "file": line_changes.path,
            "line": line_changes.header.old_start,
            "ids": ids,
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False, None


def _read_file_review_summary() -> dict | None:
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    try:
        fresh = selected_change_matches_review_state(review_state)
    except Exception:
        fresh = False
    return {
        "source": review_state.source.value,
        "batch_name": review_state.batch_name,
        "file": review_state.file_path,
        "page_spec": review_state.page_spec,
        "shown_pages": list(review_state.shown_pages),
        "page_count": review_state.page_count,
        "entire_file_shown": review_state.entire_file_shown,
        "fresh": fresh,
    }


def _session_marker_path(git_dir: Path | None = None) -> Path:
    """Return the active-session marker path without creating state directories."""
    state_dir = git_dir / "git-stage-batch" if git_dir is not None else get_state_directory_path()
    return state_dir / "session" / "abort" / "head.txt"


def _git_directory_for_prompt() -> Path | None:
    """Return the git directory for prompt rendering, or None outside a repo."""
    try:
        result = run_git_command(["rev-parse", "--absolute-git-dir"], check=False, requires_index_lock=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    git_dir = result.stdout.strip()
    return Path(git_dir) if git_dir else None


def _read_status_summary() -> dict:
    """Read the complete machine-readable status summary for an active session."""
    iteration = get_iteration_count()

    included_count = count_nonblank_text_file_lines(get_included_hunks_file_path())
    discarded_count = count_nonblank_text_file_lines(get_discarded_hunks_file_path())

    skipped_hunks = []
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    if jsonl_path.exists():
        for line in stream_text_file_lines(jsonl_path):
            if line.strip():
                try:
                    skipped_hunks.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed lines

    has_selected, selected_summary = _read_selected_change_summary()
    file_review_summary = _read_file_review_summary()

    remaining_estimate = estimate_remaining_hunks()
    status_value = "in_progress" if has_selected or remaining_estimate > 0 else "complete"

    return {
        "session": {
            "active": True,
            "iteration": iteration,
            "status": status_value,
            "in_progress": status_value == "in_progress",
        },
        "selected_change": selected_summary,
        "file_review": file_review_summary,
        "progress": {
            "included": included_count,
            "skipped": len(skipped_hunks),
            "discarded": discarded_count,
            "remaining": remaining_estimate,
        },
        "skipped_hunks": skipped_hunks,
    }


def _prompt_field_names(prompt_format: str) -> set[str]:
    """Return top-level field names used by a status prompt format string."""
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(prompt_format)
        for _literal_text, field_name, _format_spec, _conversion in parsed:
            if field_name is None:
                continue
            if field_name == "":
                raise CommandError(_("Status prompt format cannot use positional fields."))
            field_name = field_name.split(".", 1)[0].split("[", 1)[0]
            if field_name not in _PROMPT_FIELDS:
                raise CommandError(
                    _("Unknown status prompt field '{field}'.").format(field=field_name)
                )
            fields.add(field_name)
    except ValueError as error:
        raise CommandError(
            _("Invalid status prompt format: {error}").format(error=str(error))
        ) from error
    return fields


def _prompt_values(summary: dict | None = None) -> dict:
    """Return values available to `status --for-prompt` format strings."""
    if summary is None:
        return {"active": True}

    session = summary["session"]
    progress = summary["progress"]
    selected = summary["selected_change"] or {}
    file_review = summary["file_review"] or {}
    progress_status = session["status"]
    progress_label = _("in progress") if progress_status == "in_progress" else _("complete")
    processed = progress["included"] + progress["skipped"] + progress["discarded"]
    total = processed + progress["remaining"]
    status = "STAGING"

    return {
        "active": session["active"],
        "change_type": selected.get("change_type") or "",
        "discarded": progress["discarded"],
        "file_review_batch": file_review.get("batch_name") or "",
        "file_review_fresh": file_review.get("fresh", ""),
        "file_review_source": file_review.get("source") or "",
        "included": progress["included"],
        "in_progress": session["in_progress"],
        "iteration": session["iteration"],
        "processed": processed,
        "progress_label": progress_label,
        "progress_status": progress_status,
        "remaining": progress["remaining"],
        "selected_file": selected.get("file") or "",
        "selected_ids": format_id_range(selected.get("ids") or []),
        "selected_kind": selected.get("kind") or "",
        "selected_line": selected.get("line") or "",
        "skipped": progress["skipped"],
        "status": status,
        "status_label": status,
        "total": total,
    }


def _render_prompt_status(prompt_format: str, summary: dict | None = None) -> str:
    """Render a prompt status segment for an active session."""
    fields = _prompt_field_names(prompt_format)
    values = _prompt_values(summary if fields - _LIGHT_PROMPT_FIELDS else None)
    try:
        return prompt_format.format_map(values)
    except KeyError as error:
        raise CommandError(
            _("Unknown status prompt field '{field}'.").format(field=error.args[0])
        ) from error
    except ValueError as error:
        raise CommandError(
            _("Invalid status prompt format: {error}").format(error=str(error))
        ) from error


def command_status(*, porcelain: bool = False, prompt_format: str | None = None) -> None:
    """Show session progress and selected state.

    Args:
        porcelain: If True, output JSON for scripting instead of human-readable text
        prompt_format: If set, render this format string only for active sessions
    """
    if porcelain and prompt_format is not None:
        raise CommandError(_("Cannot use --porcelain with --for-prompt."))

    if prompt_format is not None:
        git_dir = _git_directory_for_prompt()
        if git_dir is None or not _session_marker_path(git_dir).exists():
            return
    else:
        require_git_repository()

    # Only treat an active abort marker as a live session. The state directory
    # can persist after cleanup because batch metadata is intentionally kept.
    if prompt_format is None and not _session_marker_path().exists():
        if porcelain:
            print(json.dumps({"session": {"active": False}}))
        else:
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        return

    if prompt_format is not None:
        fields = _prompt_field_names(prompt_format)
        output = _read_status_summary() if fields - _LIGHT_PROMPT_FIELDS else None
        print(_render_prompt_status(prompt_format, output), end="")
        return

    output = _read_status_summary()

    if porcelain:
        print(json.dumps(output, indent=2))
    else:
        # Human-readable progress report
        iteration = output["session"]["iteration"]
        status_value = output["session"]["status"]
        selected_summary = output["selected_change"]
        file_review_summary = output["file_review"]
        progress = output["progress"]
        skipped_hunks = output["skipped_hunks"]
        status_label = _("in progress") if status_value == "in_progress" else _("complete")
        print(_("Session: iteration {iteration} ({status})").format(
            iteration=iteration,
            status=status_label,
        ))
        print()

        if selected_summary:
            ids_str = format_id_range(selected_summary["ids"])
            print(_selected_kind_label(selected_summary.get("kind")))
            if selected_summary.get("line") is None:
                print(_("  {file}").format(file=selected_summary["file"]))
            else:
                print(_("  {file}:{line}").format(
                    file=selected_summary["file"],
                    line=selected_summary["line"],
                ))
            if ids_str:
                print(_("  [#{ids}]").format(ids=ids_str))
            if selected_summary.get("change_type"):
                print(_("  {change_type}").format(change_type=selected_summary["change_type"]))
            print()

        if file_review_summary:
            print(_("Last file review:"))
            source = file_review_summary["source"]
            if file_review_summary["batch_name"]:
                source = _("batch {name}").format(name=file_review_summary["batch_name"])
            print(_("  source: {source}").format(source=source))
            print(
                _("  pages: {pages}/{count}").format(
                    pages=format_id_range(file_review_summary["shown_pages"]),
                    count=file_review_summary["page_count"],
                )
            )
            if not file_review_summary["entire_file_shown"]:
                print(_("  partial review; bare whole-file actions will require confirmation by command"))
            if not file_review_summary["fresh"]:
                print(_("  stale; run show again before using pathless line actions"))
            print()

        print(_("Progress this iteration:"))
        print(_("  Included:  {count} hunks").format(count=progress["included"]))
        print(_("  Skipped:   {count} hunks").format(count=len(skipped_hunks)))
        print(_("  Discarded: {count} hunks").format(count=progress["discarded"]))
        print(_("  Remaining: ~{count} hunks").format(count=progress["remaining"]))

        if skipped_hunks:
            print()
            print(_("Skipped hunks:"))
            for hunk in skipped_hunks:
                ids_str = format_id_range(hunk.get("ids", []))
                if hunk.get("line") is None:
                    print(_("  {file}").format(file=hunk["file"]))
                elif ids_str:
                    print(_("  {file}:{line} [#{ids}]").format(
                        file=hunk["file"],
                        line=hunk["line"],
                        ids=ids_str,
                    ))
                else:
                    print(_("  {file}:{line}").format(file=hunk["file"], line=hunk["line"]))
