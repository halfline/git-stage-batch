"""Machine-readable status summary assembly."""

from __future__ import annotations

import json

from .batch_selected_changes import (
    load_current_selected_batch_binary_file,
)
from .change_freshness import (
    binary_file_change_is_stale,
    gitlink_change_is_stale,
    rename_change_is_stale,
    text_deletion_change_is_stale,
)
from .file_review.freshness import selected_change_matches_review_state
from .file_review.records import FileReviewAction, ReviewSource
from .file_review.selection_validation import shown_review_selections_for_action
from .file_review.state import read_last_file_review_state
from .line_state import load_line_changes_from_state
from .remaining_hunks import estimate_remaining_hunks as _estimate_remaining_hunks
from .selected_change.file_changes import (
    load_selected_binary_file,
    load_selected_gitlink_change,
    load_selected_rename_change,
    load_selected_text_deletion_change,
)
from .selected_change.snapshots import snapshots_are_stale
from .selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from .session import get_iteration_count
from ..utils.file_io import count_nonblank_text_file_lines, stream_text_file_lines
from ..utils.paths import (
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_line_changes_json_file_path,
    get_selected_hunk_patch_file_path,
    get_skipped_hunks_jsonl_file_path,
)


def read_status_summary() -> dict:
    """Read the complete machine-readable status summary for an active session."""
    iteration = get_iteration_count()

    included_count = count_nonblank_text_file_lines(get_included_hunks_file_path())
    discarded_count = count_nonblank_text_file_lines(get_discarded_hunks_file_path())
    skipped_hunks = _read_skipped_hunks()

    has_selected, selected_summary = _read_selected_change_summary()
    file_review_summary = _read_file_review_summary()

    remaining_estimate = _estimate_remaining_hunks()
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


def _read_skipped_hunks() -> list[dict]:
    skipped_hunks = []
    jsonl_path = get_skipped_hunks_jsonl_file_path()
    if not jsonl_path.exists():
        return skipped_hunks

    for line in stream_text_file_lines(jsonl_path):
        if not line.strip():
            continue
        try:
            skipped_hunks.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    return skipped_hunks


def _selected_change_is_stale(
    selected_kind: SelectedChangeKind | None,
    file_path: str,
) -> bool:
    """Return whether selected state should be treated as stale by status."""
    if selected_kind in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY):
        return False
    return snapshots_are_stale(file_path)


def _read_batch_review_display_ids(file_path: str) -> list[int]:
    """Return user-visible gutter IDs for the current batch file review."""
    review_state = read_last_file_review_state(clear_invalid=False)
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
    review_state = read_last_file_review_state(clear_invalid=False)
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
    if selected_kind == SelectedChangeKind.RENAME:
        rename_change = load_selected_rename_change()
        if rename_change is None:
            return False, None
        if rename_change_is_stale(rename_change):
            return False, None
        return True, {
            "kind": selected_kind.value,
            "file": rename_change.new_path,
            "line": None,
            "ids": [],
            "change_type": "renamed",
            "old_path": rename_change.old_path,
            "new_path": rename_change.new_path,
        }

    if selected_kind == SelectedChangeKind.DELETION:
        deletion_change = load_selected_text_deletion_change()
        if deletion_change is None:
            return False, None
        if text_deletion_change_is_stale(deletion_change):
            return False, None
        return True, {
            "kind": selected_kind.value,
            "file": deletion_change.path(),
            "line": None,
            "ids": [],
            "change_type": "deleted",
        }

    if selected_kind in (SelectedChangeKind.GITLINK, SelectedChangeKind.BATCH_GITLINK):
        gitlink_change = load_selected_gitlink_change()
        if gitlink_change is None:
            return False, None
        if selected_kind == SelectedChangeKind.GITLINK and gitlink_change_is_stale(
            gitlink_change
        ):
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
        binary_file = (
            load_current_selected_batch_binary_file(clear_stale=False)
            if selected_kind == SelectedChangeKind.BATCH_BINARY
            else load_selected_binary_file()
        )
        if binary_file is None:
            return False, None
        if selected_kind == SelectedChangeKind.BINARY and binary_file_change_is_stale(
            binary_file
        ):
            return False, None
        file_path = binary_file.path()
        return True, {
            "kind": selected_kind.value,
            "file": file_path,
            "line": None,
            "ids": [],
            "change_type": binary_file.change_type,
        }

    if (
        not get_selected_hunk_patch_file_path().exists()
        or not get_line_changes_json_file_path().exists()
    ):
        return False, None

    try:
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            return False, None
        if selected_kind == SelectedChangeKind.BATCH_FILE:
            review_state = read_last_file_review_state(clear_invalid=False)
            if review_state is not None:
                try:
                    if not selected_change_matches_review_state(review_state):
                        return False, None
                except Exception:
                    return False, None
        if _selected_change_is_stale(selected_kind, line_changes.path):
            return False, None
        kind_value = (
            selected_kind.value
            if selected_kind is not None
            else SelectedChangeKind.HUNK.value
        )
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
    review_state = read_last_file_review_state(clear_invalid=False)
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
