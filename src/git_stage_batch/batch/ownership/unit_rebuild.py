"""Rebuild batch ownership metadata from ownership units."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.line_selection import LineRangeBuilder
from .claims import (
    format_ownership_line_set,
    presence_claims_from_source_lines,
)
from .model import BatchOwnership
from .replacement_units import ReplacementUnit, normalize_replacement_units
from .unit_types import (
    OwnershipUnit as _UnitRecord,
    OwnershipUnitKind as _UnitKind,
)


def rebuild_ownership_from_units(
    units: Sequence[_UnitRecord],
    *,
    normalize_replacement_metadata: bool = True,
) -> BatchOwnership:
    """Rebuild BatchOwnership from semantic ownership units.

    ``normalize_replacement_metadata=False`` is a trusted fast path only for
    validated, disjoint units reconstructed from already-normalized ownership
    metadata. Arbitrary or independently assembled units must retain the
    default normalization so overlaps and invalid references fail closed.
    """
    all_presence_lines = LineRangeBuilder()
    all_presence_references = {}
    all_deletions = []
    replacement_units: list[ReplacementUnit] = []

    for unit in units:
        for range_start, range_end in unit.claimed_source_lines.ranges():
            all_presence_lines.add_range(range_start, range_end)
        all_presence_references.update(
            {
                line: reference
                for line, reference in unit.baseline_references.items()
                if line in unit.claimed_source_lines
            }
        )
        deletion_indices = []
        for deletion in unit.deletion_claims:
            all_deletions.append(deletion)
            deletion_indices.append(len(all_deletions) - 1)
        if unit.kind == _UnitKind.REPLACEMENT and unit.preserves_replacement_unit:
            replacement_units.append(
                ReplacementUnit(
                    presence_lines=format_ownership_line_set(unit.claimed_source_lines),
                    deletion_indices=deletion_indices,
                    origin_evidence=unit.replacement_origin_evidence,
                )
            )

    normalized_replacement_units = (
        normalize_replacement_units(
            replacement_units,
            deletion_count=len(all_deletions),
        )
        if normalize_replacement_metadata
        else replacement_units
    )
    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            all_presence_lines.finish(),
            all_presence_references,
        ),
        deletions=all_deletions,
        replacement_units=normalized_replacement_units,
    )
