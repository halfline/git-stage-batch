"""Persisted safety state for page-aware file reviews."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from ..core.actionable_changes import ActionableSelectionReason
from ..core.line_selection import parse_line_selection
from ..core.models import ReviewActionGroup
from ..data.line_state import convert_line_changes_to_serializable_dict, load_line_changes_from_state
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_file_bytes, read_text_file_contents, write_text_file_contents
from ..utils.paths import (
    get_index_snapshot_file_path,
    get_last_file_review_state_file_path,
    get_working_tree_snapshot_file_path,
)
from .hunk_tracking import SelectedChangeKind, get_selected_change_file_path, read_selected_change_kind


class ReviewSource(str, Enum):
    """Source of the selected file review."""

    FILE_VS_HEAD = "file-vs-head"
    UNSTAGED = "unstaged"
    BATCH = "batch"


class FileReviewAction(str, Enum):
    """Commands that may act on a file-review selection."""

    INCLUDE = "include"
    SKIP = "skip"
    DISCARD = "discard"
    INCLUDE_TO_BATCH = "include-to-batch"
    DISCARD_TO_BATCH = "discard-to-batch"
    INCLUDE_FROM_BATCH = "include-from-batch"
    DISCARD_FROM_BATCH = "discard-from-batch"
    APPLY_FROM_BATCH = "apply-from-batch"
    RESET_FROM_BATCH = "reset-from-batch"


@dataclass(frozen=True)
class FileReviewSelectionState:
    """One complete actionable selection shown by a file review."""

    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    change_index: int
    first_page: int
    last_page: int
    reason: ActionableSelectionReason
    actions: tuple[FileReviewAction, ...]


@dataclass(frozen=True)
class FileReviewState:
    """Persisted identity and safety state for the last file review."""

    source: ReviewSource
    batch_name: str | None
    file_path: str
    page_spec: str
    shown_pages: tuple[int, ...]
    page_count: int
    entire_file_shown: bool
    selections: tuple[FileReviewSelectionState, ...]
    selected_change_kind: SelectedChangeKind
    selected_file_fingerprint: str
    diff_fingerprint: str


@dataclass(frozen=True)
class ImplicitLiveToBatchFileActionResult:
    """Validated target for `--to --file` with no path."""

    reviewed_file: str | None = None
    review_state: FileReviewState | None = None
    should_stop: bool = False


@dataclass(frozen=True)
class ActionScopeResolution:
    """Resolved file-review scope for a command prologue."""

    file: str | None
    review_state: FileReviewState | None = None
    should_stop: bool = False


class ReviewScopedSelectionError(CommandError):
    """Raised when a pathless line action is not valid for the current review."""


def _coerce_review_source(source: ReviewSource | str) -> ReviewSource:
    return source if isinstance(source, ReviewSource) else ReviewSource(source)


def _coerce_review_action(action: FileReviewAction | str) -> FileReviewAction:
    return action if isinstance(action, FileReviewAction) else FileReviewAction(action)


def _json_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def fingerprint_selected_file_view(
    *,
    source: ReviewSource,
    batch_name: str | None,
    file_path: str,
    selected_change_kind: SelectedChangeKind,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
    line_changes=None,
) -> str:
    """Fingerprint the selected file view and its current line ID space."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    snapshots = {}
    for name, path in (
        ("index", get_index_snapshot_file_path()),
        ("working_tree", get_working_tree_snapshot_file_path()),
    ):
        snapshots[name] = sha256(read_file_bytes(path)).hexdigest() if path.exists() else None
    return _json_hash(
        {
            "source": source,
            "batch_name": batch_name,
            "file_path": file_path,
            "selected_change_kind": selected_change_kind.value,
            "snapshots": snapshots,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
            "gutter_to_selection_id": gutter_to_selection_id,
            "actionable_selection_groups": actionable_selection_groups,
            "review_action_groups": [
                {
                    "display_ids": group.display_ids,
                    "selection_ids": group.selection_ids,
                    "actions": group.actions,
                    "reason": group.reason,
                }
                for group in (review_action_groups or ())
            ],
        }
    )


def compute_current_file_review_diff_fingerprint(file_path: str, line_changes=None) -> str:
    """Fingerprint the cached selected file diff for freshness checks."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    return _json_hash(
        {
            "file_path": file_path,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
        }
    )


def write_last_file_review_state(review_state: FileReviewState) -> None:
    """Persist the last file review state."""
    write_text_file_contents(
        get_last_file_review_state_file_path(),
        json.dumps(asdict(review_state), ensure_ascii=False, indent=0),
    )


def read_last_file_review_state() -> FileReviewState | None:
    """Read the last file review state, if present and valid."""
    path = get_last_file_review_state_file_path()
    if not path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(path))
        selections = tuple(
            FileReviewSelectionState(
                display_ids=tuple(selection["display_ids"]),
                selection_ids=tuple(selection["selection_ids"]),
                change_index=selection["change_index"],
                first_page=selection["first_page"],
                last_page=selection["last_page"],
                reason=ActionableSelectionReason(selection["reason"]),
                actions=tuple(FileReviewAction(action) for action in selection["actions"]),
            )
            for selection in data.get("selections", [])
        )
        return FileReviewState(
            source=ReviewSource(data["source"]),
            batch_name=data.get("batch_name"),
            file_path=data["file_path"],
            page_spec=data["page_spec"],
            shown_pages=tuple(data["shown_pages"]),
            page_count=data["page_count"],
            entire_file_shown=data["entire_file_shown"],
            selections=selections,
            selected_change_kind=SelectedChangeKind(data["selected_change_kind"]),
            selected_file_fingerprint=data["selected_file_fingerprint"],
            diff_fingerprint=data["diff_fingerprint"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        clear_last_file_review_state()
        return None


def clear_last_file_review_state() -> None:
    """Remove the last file review state."""
    get_last_file_review_state_file_path().unlink(missing_ok=True)


def clear_last_file_review_state_if_file_matches(file_path: str) -> None:
    """Remove the last file review state if it belongs to the given file."""
    review_state = read_last_file_review_state()
    if review_state is not None and review_state.file_path == file_path:
        clear_last_file_review_state()


def line_action_came_from_partial_review(review_state: FileReviewState | None) -> bool:
    """Return whether a line action was validated by a partial file review."""
    return review_state is not None and not review_state.entire_file_shown


def finish_review_scoped_line_action(
    review_state: FileReviewState | None,
    *,
    file_path: str | None = None,
) -> None:
    """Clear review state after a line action unless a partial review must guard follow-ups."""
    if line_action_came_from_partial_review(review_state):
        return
    if file_path is None:
        clear_last_file_review_state()
    else:
        clear_last_file_review_state_if_file_matches(file_path)
