"""Build saved state for file review pages."""

from __future__ import annotations

from collections.abc import Iterable

from ...core.actionable_changes import ActionableSelectionReason
from ...core.line_selection import LineSelection, coerce_line_ranges
from ...core.models import LineLevelChange, ReviewActionGroup
from .fingerprints import (
    compute_current_file_review_diff_fingerprint,
    fingerprint_selected_file_view,
)
from .records import (
    FileReviewAction,
    FileReviewSelectionState,
    FileReviewState,
    ReviewSource,
)
from .pages import normalize_page_spec, parse_page_selection
from ..selected_change.store import SelectedChangeKind
from .action_selections import (
    change_index_containing_review_display_ids,
    change_is_live_splittable,
    display_ids_for_change_pages,
    pages_containing_review_display_ids,
    selection_ids_for_display_ids,
)
from .model import FileReviewModel, ReviewChange


def _coerce_actionable_reason(reason: str) -> ActionableSelectionReason:
    try:
        return ActionableSelectionReason(reason)
    except ValueError:
        return ActionableSelectionReason.SIMPLE


def resolve_default_review_pages(
    model: FileReviewModel,
    *,
    requested_page_spec: str | None,
    previous_selection: LineLevelChange | None = None,
) -> tuple[int, ...]:
    """Choose requested pages, the current change's page, or page 1."""
    page_count = len(model.pages)
    if requested_page_spec is not None:
        return parse_page_selection(
            requested_page_spec,
            page_count,
            model.line_changes.path,
        )
    if page_count <= 1:
        return (1,)
    if (
        previous_selection is not None
        and previous_selection.path == model.line_changes.path
    ):
        for change in model.changes:
            if _change_overlaps_line_change(change, previous_selection):
                return (change.first_page,)
    return (1,)


def _change_overlaps_line_change(
    change: ReviewChange, line_changes: LineLevelChange
) -> bool:
    old_numbers = [
        line.old_line_number
        for line in line_changes.lines
        if line.kind != "+" and line.old_line_number is not None
    ]
    new_numbers = [
        line.new_line_number
        for line in line_changes.lines
        if line.kind != "-" and line.new_line_number is not None
    ]
    return _ranges_overlap(
        change.old_start,
        change.old_end,
        min(old_numbers, default=None),
        max(old_numbers, default=None),
    ) or _ranges_overlap(
        change.new_start,
        change.new_end,
        min(new_numbers, default=None),
        max(new_numbers, default=None),
    )


def _ranges_overlap(
    left_start: int | None,
    left_end: int | None,
    right_start: int | None,
    right_end: int | None,
) -> bool:
    if (
        left_start is None
        or left_end is None
        or right_start is None
        or right_end is None
    ):
        return False
    return left_start <= right_end and right_start <= left_end


def _supplemental_batch_review_selections(
    model: FileReviewModel,
    *,
    visible_display_ids: LineSelection | None,
) -> tuple[FileReviewSelectionState, ...]:
    """Save each batch group that can be selected on its own.

    A diff can show several independent batch groups as one change. Saving
    each group's actions lets a later command select that group without also
    selecting the rest of the displayed change.
    """
    selections: list[FileReviewSelectionState] = []
    primary_reset_groups = {
        change.display_ids
        for change in model.changes
        if FileReviewAction.RESET_FROM_BATCH.value in change.actions
    }
    for group in model.review_action_groups:
        if not group.display_ids:
            continue
        if group.display_ids in primary_reset_groups:
            continue
        if visible_display_ids is not None and not coerce_line_ranges(
            group.display_ids
        ).is_subset_of(visible_display_ids):
            continue
        if FileReviewAction.RESET_FROM_BATCH.value not in group.actions:
            continue
        pages = pages_containing_review_display_ids(model, group.display_ids)
        if not pages:
            continue
        selections.append(
            FileReviewSelectionState(
                display_ids=group.display_ids,
                selection_ids=group.selection_ids,
                change_index=change_index_containing_review_display_ids(
                    model,
                    group.display_ids,
                ),
                first_page=pages[0],
                last_page=pages[-1],
                reason=_coerce_actionable_reason(group.reason),
                actions=tuple(FileReviewAction(action) for action in group.actions),
                pages=pages,
            )
        )
    return tuple(selections)


def make_file_review_state(
    model: FileReviewModel,
    *,
    source: ReviewSource,
    batch_name: str | None,
    shown_pages: tuple[int, ...],
    selected_change_kind: SelectedChangeKind,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
    visible_display_ids: LineSelection | Iterable[int] | None = None,
    entire_file_shown: bool | None = None,
) -> FileReviewState:
    """Save the selections shown on the requested pages."""
    visible_display_selection = (
        coerce_line_ranges(visible_display_ids)
        if visible_display_ids is not None
        else None
    )
    page_count = len(model.pages)
    shown_page_set = set(shown_pages)
    default_actions = (
        (
            FileReviewAction.INCLUDE_FROM_BATCH,
            FileReviewAction.DISCARD_FROM_BATCH,
            FileReviewAction.APPLY_FROM_BATCH,
            FileReviewAction.RESET_FROM_BATCH,
        )
        if source == ReviewSource.BATCH
        else (
            FileReviewAction.INCLUDE,
            FileReviewAction.SKIP,
            FileReviewAction.DISCARD,
            FileReviewAction.INCLUDE_TO_BATCH,
            FileReviewAction.DISCARD_TO_BATCH,
        )
    )
    selections = []
    for change in model.changes:
        if not change.display_ids:
            continue
        is_splittable = source != ReviewSource.BATCH and change_is_live_splittable(
            change
        )
        if is_splittable:
            display_ids = display_ids_for_change_pages(model, change, shown_pages)
            if (
                not display_ids
                and change.reason == ActionableSelectionReason.REPLACEMENT
            ):
                display_ids = change.display_ids
        else:
            display_ids = change.display_ids
        if not display_ids:
            continue
        if visible_display_selection is not None:
            if is_splittable:
                display_ids = tuple(
                    display_id
                    for display_id in display_ids
                    if display_id in visible_display_selection
                )
                if not display_ids:
                    continue
            elif not coerce_line_ranges(display_ids).is_subset_of(
                visible_display_selection
            ):
                continue
        pages = pages_containing_review_display_ids(
            model,
            display_ids,
        )
        if not pages:
            continue
        selection_actions = (
            tuple(FileReviewAction(action) for action in change.actions)
            if change.actions
            else default_actions
        )
        selections.append(
            FileReviewSelectionState(
                display_ids=display_ids,
                selection_ids=(
                    selection_ids_for_display_ids(model, display_ids)
                    if is_splittable
                    else change.selection_ids
                ),
                change_index=change_index_containing_review_display_ids(
                    model,
                    display_ids,
                ),
                first_page=pages[0],
                last_page=pages[-1],
                reason=change.reason,
                actions=selection_actions,
                pages=pages,
                is_splittable=is_splittable,
            )
        )
    if source == ReviewSource.BATCH and review_action_groups is not None:
        selections.extend(
            _supplemental_batch_review_selections(
                model,
                visible_display_ids=visible_display_selection,
            )
        )
    computed_entire_file_shown = shown_page_set == set(range(1, page_count + 1))
    return FileReviewState(
        source=source,
        batch_name=batch_name,
        file_path=model.line_changes.path,
        page_spec=normalize_page_spec(shown_pages, page_count),
        shown_pages=shown_pages,
        page_count=page_count,
        entire_file_shown=(
            computed_entire_file_shown
            if entire_file_shown is None
            else entire_file_shown
        ),
        selections=tuple(selections),
        selected_change_kind=selected_change_kind,
        selected_file_fingerprint=fingerprint_selected_file_view(
            source=source,
            batch_name=batch_name,
            file_path=model.line_changes.path,
            selected_change_kind=selected_change_kind,
            gutter_to_selection_id=gutter_to_selection_id,
            actionable_selection_groups=actionable_selection_groups,
            review_action_groups=review_action_groups,
            line_changes=model.line_changes,
        ),
        diff_fingerprint=compute_current_file_review_diff_fingerprint(
            model.line_changes.path,
            line_changes=model.line_changes,
        ),
    )
