"""Actionable selection helpers for file review output."""

from __future__ import annotations

from ..core.actionable_changes import ActionableSelection, ActionableSelectionReason
from ..core.models import LineEntry
from ..data.file_review.records import ReviewSource
from .file_review_display_ids import display_ids_for_rows
from .file_review_model import FileReviewModel, ReviewChange


def change_is_live_splittable(change: ReviewChange) -> bool:
    """Return whether live explicit line ranges may select part of a change."""
    return (
        _change_is_presence_only(change)
        or change.reason == ActionableSelectionReason.REPLACEMENT
    )


def _change_is_presence_only(change: ReviewChange) -> bool:
    saw_changed_row = False
    for row in change.rows:
        if row.kind not in ("+", "-") or row.id is None:
            continue
        saw_changed_row = True
        if row.kind != "+":
            return False
    return saw_changed_row


def pages_containing_review_display_ids(
    model: FileReviewModel,
    display_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Return review pages containing all of the requested display IDs."""
    wanted = set(display_ids)
    found: set[int] = set()
    pages: set[int] = set()
    for page in model.pages:
        for fragment in page.changes:
            for row in fragment.rows:
                display_id = _display_id_for_row(model, row)
                if display_id in wanted:
                    found.add(display_id)
                    pages.add(page.page)
    if found != wanted:
        return tuple()
    return tuple(sorted(pages))


def change_index_containing_review_display_ids(
    model: FileReviewModel,
    display_ids: tuple[int, ...],
) -> int:
    """Return a stable nearby change index for supplemental review selections."""
    wanted = set(display_ids)
    for page in model.pages:
        for fragment in page.changes:
            row_display_ids = {
                _display_id_for_row(model, row)
                for row in fragment.rows
                if row.id is not None
            }
            if wanted & row_display_ids:
                return fragment.change.index
    return 0


def _display_id_for_row(model: FileReviewModel, row: LineEntry) -> int | None:
    if row.id is None:
        return None
    if model.display_id_by_selection_id is None:
        return row.id
    return model.display_id_by_selection_id.get(row.id)


def selection_ids_for_display_ids(
    model: FileReviewModel,
    display_ids: tuple[int, ...],
) -> tuple[int, ...]:
    """Translate review display IDs back to line-selection IDs."""
    if model.display_id_by_selection_id is None:
        return display_ids
    selection_id_by_display_id = {
        display_id: selection_id
        for selection_id, display_id in model.display_id_by_selection_id.items()
    }
    return tuple(
        selection_id_by_display_id[display_id]
        for display_id in display_ids
        if display_id in selection_id_by_display_id
    )


def display_ids_for_change_pages(
    model: FileReviewModel,
    change: ReviewChange,
    shown_pages: tuple[int, ...],
) -> tuple[int, ...]:
    """Return the display IDs from one visual change on the shown pages."""
    display_ids: list[int] = []
    seen_display_ids: set[int] = set()
    for page_number in shown_pages:
        page = model.pages[page_number - 1]
        for fragment in page.changes:
            if fragment.change.index != change.index:
                continue
            for display_id in display_ids_for_rows(
                fragment.rows,
                model.display_id_by_selection_id,
            ):
                if display_id in seen_display_ids:
                    continue
                display_ids.append(display_id)
                seen_display_ids.add(display_id)
    return tuple(display_ids)


def shown_line_action_selections(
    model: FileReviewModel,
    shown_pages: tuple[int, ...],
    *,
    source: ReviewSource,
) -> list[ActionableSelection]:
    """Return line-action selections fully contained by the shown pages."""
    shown_page_set = set(shown_pages)
    selections: list[ActionableSelection] = []
    for change in model.changes:
        if not change.display_ids:
            continue
        can_split_presence = (
            source != ReviewSource.BATCH
            and _change_is_presence_only(change)
        )
        display_ids = (
            display_ids_for_change_pages(model, change, shown_pages)
            if can_split_presence else
            change.display_ids
        )
        if not display_ids:
            continue
        pages = pages_containing_review_display_ids(model, display_ids)
        if not pages or not set(pages).issubset(shown_page_set):
            continue
        selections.append(
            ActionableSelection(
                display_ids=display_ids,
                selection_ids=(
                    selection_ids_for_display_ids(model, display_ids)
                    if can_split_presence else
                    change.selection_ids
                ),
                reason=change.reason,
                note=change.note,
                actions=change.actions,
            )
        )
    return selections
