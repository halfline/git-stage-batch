"""Replacement-unit metadata normalization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.line_selection import LineRanges
from .ownership_claims import (
    format_ownership_line_set,
    parse_ownership_line_ranges,
)

if TYPE_CHECKING:
    from .ownership import ReplacementUnit, ReplacementUnitOrigin


def normalize_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Drop invalid references and coalesce overlapping replacement units."""
    from .ownership import ReplacementUnit

    components: list[tuple[LineRanges, set[int], ReplacementUnitOrigin | None]] = []

    for unit in replacement_units:
        claimed = parse_ownership_line_ranges(unit.presence_lines)
        deletion_indices = {
            index
            for index in unit.deletion_indices
            if type(index) is int and 0 <= index < deletion_count
        }
        if not claimed or not deletion_indices:
            continue
        origin = _normalize_replacement_unit_origin(unit.origin)

        overlapping_component_indices = [
            index
            for index, (
                component_claimed,
                component_deletions,
                _component_origin,
            ) in enumerate(components)
            if (
                component_claimed.intersection(claimed)
                or component_deletions & deletion_indices
            )
        ]
        if not overlapping_component_indices:
            components.append((claimed, set(deletion_indices), origin))
            continue

        target_index = overlapping_component_indices[0]
        target_claimed, target_deletions, target_origin = components[target_index]
        target_claimed = target_claimed.union(claimed)
        target_deletions.update(deletion_indices)
        target_origin = _merge_replacement_unit_origins(target_origin, origin)

        for source_index in reversed(overlapping_component_indices[1:]):
            source_claimed, source_deletions, source_origin = components[source_index]
            target_claimed = target_claimed.union(source_claimed)
            target_deletions.update(source_deletions)
            target_origin = _merge_replacement_unit_origins(
                target_origin,
                source_origin,
            )
            del components[source_index]
        components[target_index] = (target_claimed, target_deletions, target_origin)

    return [
        ReplacementUnit(
            presence_lines=format_ownership_line_set(claimed),
            deletion_indices=sorted(deletion_indices),
            origin=origin,
        )
        for claimed, deletion_indices, origin in components
    ]


def _normalize_replacement_unit_origin(
    origin: ReplacementUnitOrigin | None,
) -> ReplacementUnitOrigin | None:
    """Return valid original replacement context, or None."""
    if origin is None:
        return None
    if (
        type(origin.old_start) is not int
        or type(origin.old_end) is not int
        or type(origin.new_start) is not int
        or type(origin.new_end) is not int
        or origin.old_start > origin.old_end
        or origin.new_start > origin.new_end
    ):
        return None
    return origin


def _merge_replacement_unit_origins(
    left: ReplacementUnitOrigin | None,
    right: ReplacementUnitOrigin | None,
) -> ReplacementUnitOrigin | None:
    """Keep parent context only when coalesced units agree on it."""
    if left == right:
        return left
    if left is None:
        return right
    if right is None:
        return left
    return None
