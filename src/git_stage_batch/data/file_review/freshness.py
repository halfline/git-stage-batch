"""Freshness checks for persisted file review state."""

from __future__ import annotations

from ...batch.file_display import render_batch_file_display
from . import records as _records
from .fingerprints import (
    compute_current_file_review_diff_fingerprint as _compute_current_file_review_diff_fingerprint,
    fingerprint_selected_file_view as _fingerprint_selected_file_view,
)
from ..selected_change.store import (
    SelectedChangeKind,
    get_selected_change_file_path,
    read_selected_change_kind,
)
from ..selected_change.snapshots import snapshots_are_stale


def _coerce_review_action(action: _records.FileReviewAction | str) -> _records.FileReviewAction:
    return action if isinstance(action, _records.FileReviewAction) else _records.FileReviewAction(action)


def selected_change_kind_matches_review_source(
    selected_kind: SelectedChangeKind | None,
    review_state: _records.FileReviewState,
) -> bool:
    """Return whether the selected kind is compatible with the review source."""
    if review_state.source in (_records.ReviewSource.FILE_VS_HEAD, _records.ReviewSource.UNSTAGED):
        return selected_kind == SelectedChangeKind.FILE
    if review_state.source == _records.ReviewSource.BATCH:
        return selected_kind in (
            SelectedChangeKind.BATCH_FILE,
            SelectedChangeKind.BATCH_BINARY,
            SelectedChangeKind.BATCH_GITLINK,
        )
    return False


def selected_change_matches_review_state(review_state: _records.FileReviewState) -> bool:
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
    if review_state.source == _records.ReviewSource.BATCH and review_state.batch_name is not None:
        rendered = render_batch_file_display(
            review_state.batch_name,
            review_state.file_path,
        )
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
        if snapshots_are_stale(review_state.file_path):
            return False
        actionable_selection_groups = None
        review_action_groups = None

    current_selected_fingerprint = _fingerprint_selected_file_view(
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
        _compute_current_file_review_diff_fingerprint(
            review_state.file_path,
            line_changes=line_changes,
        )
        == review_state.diff_fingerprint
    )


def selected_batch_review_matches_reset_state(review_state: _records.FileReviewState) -> bool:
    """Return whether a batch review still has stable reset IDs."""
    selected_kind = read_selected_change_kind()
    if review_state.source != _records.ReviewSource.BATCH or review_state.batch_name is None:
        return False
    if not selected_change_kind_matches_review_source(selected_kind, review_state):
        return False
    if get_selected_change_file_path() != review_state.file_path:
        return False

    rendered = render_batch_file_display(
        review_state.batch_name,
        review_state.file_path,
    )
    if rendered is None:
        return False
    if (
        _compute_current_file_review_diff_fingerprint(
            review_state.file_path,
            line_changes=rendered.line_changes,
        )
        != review_state.diff_fingerprint
    ):
        return False

    current_reset_groups = [
        (group.display_ids, group.selection_ids)
        for group in rendered.review_action_groups
        if _records.FileReviewAction.RESET_FROM_BATCH.value in group.actions
    ]
    persisted_reset_groups = {
        (selection.display_ids, selection.selection_ids)
        for selection in review_state.selections
        if _records.FileReviewAction.RESET_FROM_BATCH in selection.actions
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


def review_state_matches_action(
    review_state: _records.FileReviewState,
    action: _records.FileReviewAction | str,
) -> bool:
    """Return whether a review is fresh for a specific action."""
    review_action = _coerce_review_action(action)
    if (
        review_state.source == _records.ReviewSource.BATCH
        and review_action == _records.FileReviewAction.RESET_FROM_BATCH
    ):
        return selected_batch_review_matches_reset_state(review_state)
    return selected_change_matches_review_state(review_state)
