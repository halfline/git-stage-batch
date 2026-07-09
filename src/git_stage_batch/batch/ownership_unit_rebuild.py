"""Rebuild batch ownership metadata from ownership units."""

from __future__ import annotations

from ..core.line_selection import LineRanges
from .ownership import (
    BatchOwnership,
    ReplacementUnit,
)
from .ownership_claims import (
    format_ownership_line_set,
    presence_claims_from_source_lines,
)
from .ownership_replacement_units import normalize_replacement_units
from .ownership_unit_types import (
    OwnershipUnit as _UnitRecord,
    OwnershipUnitKind as _UnitKind,
)


def rebuild_ownership_from_units(units: list[_UnitRecord]) -> BatchOwnership:
    """Rebuild BatchOwnership from semantic ownership units."""
    all_presence_lines = LineRanges.empty()
    all_deletions = []
    replacement_units: list[ReplacementUnit] = []

    for unit in units:
        all_presence_lines = all_presence_lines.union(unit.claimed_source_lines)
        deletion_indices = []
        for deletion in unit.deletion_claims:
            all_deletions.append(deletion)
            deletion_indices.append(len(all_deletions) - 1)
        if (
            unit.kind == _UnitKind.REPLACEMENT
            and unit.preserves_replacement_unit
        ):
            replacement_units.append(ReplacementUnit(
                presence_lines=format_ownership_line_set(unit.claimed_source_lines),
                deletion_indices=deletion_indices,
                origin=unit.replacement_origin,
            ))

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(all_presence_lines),
        deletions=all_deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(all_deletions),
        ),
    )
