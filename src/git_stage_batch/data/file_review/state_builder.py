"""Persisted state assembly for page-aware file review."""

from __future__ import annotations

from ...core.models import LineLevelChange
from .model import FileReviewModel, ReviewChange
from .pages import parse_page_selection


def resolve_default_review_pages(
    model: FileReviewModel,
    *,
    requested_page_spec: str | None,
    previous_selection: LineLevelChange | None = None,
) -> tuple[int, ...]:
    """Resolve explicit pages, selected-hunk anchor, or default page 1."""
    page_count = len(model.pages)
    if requested_page_spec is not None:
        return parse_page_selection(
            requested_page_spec,
            page_count,
            model.line_changes.path,
        )
    if page_count <= 1:
        return (1,)
    if previous_selection is not None and previous_selection.path == model.line_changes.path:
        for change in model.changes:
            if _change_overlaps_line_change(change, previous_selection):
                return (change.first_page,)
    return (1,)


def _change_overlaps_line_change(change: ReviewChange, line_changes: LineLevelChange) -> bool:
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
    return (
        _ranges_overlap(
            change.old_start,
            change.old_end,
            min(old_numbers, default=None),
            max(old_numbers, default=None),
        )
        or _ranges_overlap(
            change.new_start,
            change.new_end,
            min(new_numbers, default=None),
            max(new_numbers, default=None),
        )
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
