"""Display-ID selection helpers for ownership units."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.line_selection import LineRanges, LineSelection
from ..exceptions import AtomicUnitError
from ..i18n import _
from .ownership_unit_types import (
    OwnershipUnit as _UnitRecord,
)


def select_ownership_units_by_display_ids(
    units: list[_UnitRecord],
    selected_display_ids: LineSelection | Iterable[int],
) -> list[_UnitRecord]:
    """Select ownership units that match the given display line IDs.

    Validates that atomic units are not partially selected.
    """
    selected = []
    selected_display_ranges = (
        selected_display_ids
        if isinstance(selected_display_ids, LineRanges)
        else LineRanges.from_lines(selected_display_ids)
    )

    for unit in units:
        intersection = unit.display_line_ids.intersection(selected_display_ranges)

        if not intersection:
            continue
        if unit.is_atomic and intersection != unit.display_line_ids:
            raise AtomicUnitError(
                _("Cannot select only part of this change.\n"
                  "Select all related lines together: {required_ids}\n"
                  "You selected: {selected_ids}").format(
                    required_ids=unit.display_line_ids.to_line_spec(),
                    selected_ids=intersection.to_line_spec()
                ),
                required_selection_ids=unit.display_line_ids,
                unit_kind=unit.kind.value
            )
        selected.append(unit)

    return selected


def filter_ownership_units_by_display_ids(
    units: list[_UnitRecord],
    selected_display_ids: LineSelection | Iterable[int],
) -> tuple[list[_UnitRecord], list[_UnitRecord]]:
    """Filter ownership units, removing those that match display line IDs."""
    removed = select_ownership_units_by_display_ids(units, selected_display_ids)
    removed_ids = {id(unit) for unit in removed}
    remaining = [unit for unit in units if id(unit) not in removed_ids]
    return remaining, removed
