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
def selected_change_kind_matches_review_source(
    selected_kind: SelectedChangeKind | None,
    review_state: FileReviewState,
) -> bool:
    """Return whether the selected kind is compatible with the review source."""
    if review_state.source in (ReviewSource.FILE_VS_HEAD, ReviewSource.UNSTAGED):
        return selected_kind == SelectedChangeKind.FILE
    if review_state.source == ReviewSource.BATCH:
        return selected_kind in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY)
    return False


def selected_change_matches_review_state(review_state: FileReviewState) -> bool:
    """Return whether selected state still matches the persisted review state."""
    selected_kind = read_selected_change_kind()
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        return False
    if get_selected_change_file_path() != review_state.file_path:
        return False
    if selected_kind is None:
        return False
    gutter_to_selection_id = None
    line_changes = None
    if review_state.source == ReviewSource.BATCH and review_state.batch_name is not None:
        from .hunk_tracking import render_batch_file_display

        rendered = render_batch_file_display(review_state.batch_name, review_state.file_path)
        if rendered is None:
            return False
        gutter_to_selection_id = (
            rendered.review_gutter_to_selection_id
            or rendered.gutter_to_selection_id
        )
        actionable_selection_groups = rendered.actionable_selection_groups
        review_action_groups = rendered.review_action_groups or None
        line_changes = rendered.line_changes
    else:
        from .hunk_tracking import snapshots_are_stale

        if snapshots_are_stale(review_state.file_path):
            return False
        actionable_selection_groups = None
        review_action_groups = None

    current_selected_fingerprint = fingerprint_selected_file_view(
        source=review_state.source,
        batch_name=review_state.batch_name,
        file_path=review_state.file_path,
        selected_change_kind=selected_kind,
        gutter_to_selection_id=gutter_to_selection_id,
        actionable_selection_groups=actionable_selection_groups,
        review_action_groups=review_action_groups,
        line_changes=line_changes,
    )
    if current_selected_fingerprint != review_state.selected_file_fingerprint:
        return False
    return (
        compute_current_file_review_diff_fingerprint(review_state.file_path, line_changes=line_changes)
        == review_state.diff_fingerprint
    )


def selected_batch_review_matches_reset_state(review_state: FileReviewState) -> bool:
    """Return whether a batch review still has stable reset IDs."""
    selected_kind = read_selected_change_kind()
    if review_state.source != ReviewSource.BATCH or review_state.batch_name is None:
        return False
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        return False
    if get_selected_change_file_path() != review_state.file_path:
        return False

    from .hunk_tracking import render_batch_file_display

    rendered = render_batch_file_display(review_state.batch_name, review_state.file_path)
    if rendered is None:
        return False
    if (
        compute_current_file_review_diff_fingerprint(
            review_state.file_path,
            line_changes=rendered.line_changes,
        )
        != review_state.diff_fingerprint
    ):
        return False

    current_reset_groups = [
        (group.display_ids, group.selection_ids)
        for group in rendered.review_action_groups
        if FileReviewAction.RESET_FROM_BATCH.value in group.actions
    ]
    persisted_reset_groups = {
        (selection.display_ids, selection.selection_ids)
        for selection in review_state.selections
        if FileReviewAction.RESET_FROM_BATCH in selection.actions
    }

    def can_cover(
        remaining_pairs: frozenset[tuple[int, int]],
    ) -> bool:
        if not remaining_pairs:
            return True
        first_pair = min(remaining_pairs)
        for display_ids, selection_ids in current_reset_groups:
            group_pairs = frozenset(zip(display_ids, selection_ids))
            if first_pair not in group_pairs:
                continue
            if not group_pairs.issubset(remaining_pairs):
                continue
            if can_cover(remaining_pairs - group_pairs):
                return True
        return False

    return all(
        can_cover(frozenset(zip(display_ids, selection_ids)))
        for display_ids, selection_ids in persisted_reset_groups
    )


def _review_state_matches_action(
    review_state: FileReviewState,
    action: FileReviewAction | str,
) -> bool:
    """Return whether a review is fresh for a specific action."""
    review_action = _coerce_review_action(action)
    if (
        review_state.source == ReviewSource.BATCH
        and review_action == FileReviewAction.RESET_FROM_BATCH
    ):
        return selected_batch_review_matches_reset_state(review_state)
    return selected_change_matches_review_state(review_state)


def _format_pages(pages: set[int]) -> str:
    from ..core.line_selection import format_line_ids

    return format_line_ids(sorted(pages))


def shown_complete_review_selection_groups(
    review_state: FileReviewState,
    action: FileReviewAction | str,
) -> list[set[int]]:
    """Return complete actionable display-ID groups from shown review pages."""
    review_action = _coerce_review_action(action)
    shown_pages = (
        set(range(1, review_state.page_count + 1))
        if review_state.entire_file_shown else
        set(review_state.shown_pages)
    )
    return [
        set(selection.display_ids)
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(shown_pages)
    ]


def fresh_batch_review_selection_groups_for_action(
    batch_name: str,
    file_path: str,
    action: FileReviewAction | str,
) -> list[set[int]] | None:
    """Return shown review groups for a fresh matching batch review, if one is active."""
    review_state = read_last_file_review_state()
    if review_state is None:
        return None
    if review_state.source != ReviewSource.BATCH:
        return None
    if review_state.batch_name != batch_name or review_state.file_path != file_path:
        return None
    review_action = _coerce_review_action(action)
    try:
        review_is_fresh = _review_state_matches_action(review_state, review_action)
    except Exception:
        review_is_fresh = False
    if not review_is_fresh:
        raise CommandError(
            _(
                "The file review for {file} no longer matches batch '{batch}'.\n"
                "Line IDs may no longer match.\n\n"
                "Run:\n"
                "  git-stage-batch show --from {batch} --file {file}"
            ).format(
                batch=shlex.quote(batch_name),
                file=shlex.quote(file_path),
            )
        )

    return shown_complete_review_selection_groups(review_state, action)


def fresh_batch_review_display_ids_for_action(
    batch_name: str,
    file_path: str,
    action: FileReviewAction | str,
) -> set[int] | None:
    """Return shown display IDs for a fresh matching batch review, if one is active."""
    groups = fresh_batch_review_selection_groups_for_action(batch_name, file_path, action)
    if groups is None:
        return None
    return {display_id for group in groups for display_id in group}
