"""Selection validation for page-aware file reviews."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ...core.line_selection import (
    LineRangeBuilder,
    LineRanges,
    LineSelection,
    coerce_line_ranges,
)
from ...exceptions import CommandError
from ...i18n import _
from . import records as _records


def _format_line_ranges(selection: LineRanges) -> str:
    return selection.to_line_spec()


class ReviewSelectionForValidation(Protocol):
    """Selection fields needed by review-scoped validation."""

    @property
    def display_ids(self) -> tuple[int, ...]:
        """Return the IDs displayed for this selection."""

    @property
    def is_splittable(self) -> bool:
        """Return whether partial selection is valid."""


def shown_review_selections_for_action(
    review_state: _records.FileReviewState,
    action: _records.FileReviewAction | str,
) -> list[_records.FileReviewSelectionState]:
    """Return actionable selections fully contained by the shown review pages."""
    review_action = _records.coerce_review_action(action)
    shown_pages = (
        set(range(1, review_state.page_count + 1))
        if review_state.entire_file_shown
        else set(review_state.shown_pages)
    )
    return [
        selection
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(
            shown_pages
        )
    ]


def validate_review_scoped_line_selection(
    requested_ids: LineSelection | Iterable[int],
    valid_selections: Iterable[ReviewSelectionForValidation],
) -> None:
    """Validate a union of complete actionable review selections."""
    requested_ranges = coerce_line_ranges(requested_ids)
    matched_ids = LineRangeBuilder()
    partial_atomic_groups: list[LineRanges] = []
    for selection in valid_selections:
        display_id_builder = LineRangeBuilder()
        for display_id in selection.display_ids:
            display_id_builder.add_line(display_id)
        display_ids = display_id_builder.finish()
        if not display_ids:
            continue

        display_ranges = display_ids.ranges()
        if selection.is_splittable:
            for display_start, display_end in display_ranges:
                eligible_ids = requested_ranges.intersection_with_range(
                    display_start,
                    display_end,
                )
                for range_start, range_end in eligible_ids.ranges():
                    matched_ids.add_range(range_start, range_end)
            continue
        if all(
            requested_ranges.contains_range(display_start, display_end)
            for display_start, display_end in display_ranges
        ):
            eligible_ids = display_ids
        else:
            if any(
                requested_ranges.intersects_range(display_start, display_end)
                for display_start, display_end in display_ranges
            ):
                partial_atomic_groups.append(display_ids)
            continue

        for range_start, range_end in eligible_ids.ranges():
            matched_ids.add_range(range_start, range_end)

    outside_ids = requested_ranges.difference(matched_ids.finish())
    if not outside_ids:
        return

    for display_ids in partial_atomic_groups:
        if any(
            outside_ids.intersects_range(display_start, display_end)
            for display_start, display_end in display_ids.ranges()
        ):
            raise CommandError(
                _(
                    "Line selection #{requested} only partly selects a reviewed change.\nUse: --line {required}"
                ).format(
                    requested=_format_line_ranges(requested_ranges),
                    required=_format_line_ranges(display_ids),
                )
            )

    if outside_ids:
        raise CommandError(
            _(
                "Line selection #{ids} is not valid from the current file review."
            ).format(
                ids=_format_line_ranges(outside_ids),
            )
        )
