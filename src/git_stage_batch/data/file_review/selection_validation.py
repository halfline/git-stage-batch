"""Selection validation for page-aware file reviews."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...exceptions import CommandError
from ...i18n import _
from . import records as _records


def _coerce_review_action(action: _records.FileReviewAction | str) -> _records.FileReviewAction:
    return action if isinstance(action, _records.FileReviewAction) else _records.FileReviewAction(action)


def _format_line_ranges(selection: LineRanges) -> str:
    return selection.to_line_spec()


@dataclass(frozen=True)
class _ReviewValidationGroup:
    display_ids: LineRanges
    is_splittable: bool


def shown_review_selections_for_action(
    review_state: _records.FileReviewState,
    action: _records.FileReviewAction | str,
) -> list[_records.FileReviewSelectionState]:
    """Return actionable selections fully contained by the shown review pages."""
    review_action = _coerce_review_action(action)
    shown_pages = (
        set(range(1, review_state.page_count + 1))
        if review_state.entire_file_shown else
        set(review_state.shown_pages)
    )
    return [
        selection
        for selection in review_state.selections
        if review_action in selection.actions
        and set(range(selection.first_page, selection.last_page + 1)).issubset(shown_pages)
    ]


def validate_review_scoped_line_selection(
    requested_ids: LineSelection | Iterable[int],
    valid_selections: Iterable[_records.FileReviewSelectionState],
) -> None:
    """Validate a union of complete actionable review selections."""
    requested_ranges = coerce_line_ranges(requested_ids)
    groups: list[_ReviewValidationGroup] = []
    for selection in valid_selections:
        display_ids = LineRanges.from_lines(selection.display_ids)
        if display_ids:
            groups.append(
                _ReviewValidationGroup(
                    display_ids=display_ids,
                    is_splittable=selection.is_splittable,
                )
            )

    def can_cover(remaining_ids: LineRanges) -> bool:
        if not remaining_ids:
            return True
        first_id = remaining_ids.first()
        if first_id is None:
            return True
        for group in groups:
            if first_id not in group.display_ids:
                continue
            if group.is_splittable:
                selected_from_group = remaining_ids.intersection(group.display_ids)
                if can_cover(remaining_ids.difference(selected_from_group)):
                    return True
                continue
            if group.display_ids.difference(remaining_ids):
                continue
            if can_cover(remaining_ids.difference(group.display_ids)):
                return True
        return False

    if can_cover(requested_ranges):
        return

    matched_ids = LineRanges.empty()
    for group in groups:
        if group.is_splittable:
            matched_ids = matched_ids.union(requested_ranges.intersection(group.display_ids))
        elif not group.display_ids.difference(requested_ranges):
            matched_ids = matched_ids.union(group.display_ids)

    outside_ids = requested_ranges.difference(matched_ids)
    for group in groups:
        if group.is_splittable:
            continue
        overlap = outside_ids.intersection(group.display_ids)
        if overlap and overlap != group.display_ids:
            raise CommandError(
                _("Line selection #{requested} only partly selects a reviewed change.\nUse: --line {required}").format(
                    requested=_format_line_ranges(requested_ranges),
                    required=_format_line_ranges(group.display_ids),
                )
            )

    if outside_ids:
        raise CommandError(
            _("Line selection #{ids} is not valid from the current file review.").format(
                ids=_format_line_ranges(outside_ids),
            )
        )
