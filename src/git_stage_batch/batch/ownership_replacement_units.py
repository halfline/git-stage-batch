"""Replacement-unit metadata normalization."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.line_selection import LineRanges
from .ownership_claims import (
    format_ownership_line_set,
    parse_ownership_line_ranges,
)
from .ownership_references import BaselineReference


@dataclass
class ReplacementUnitOrigin:
    """Original full replacement region for a selectable replacement sub-unit.

    Split replacement units may be smaller than the file-derived replacement run
    that created them. This context records that original run so merge/discard
    code can validate placement against the parent replacement boundary instead
    of treating the selected sub-unit as an unrelated edit.
    """

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    baseline_reference: BaselineReference | None = None

    @property
    def old_line_count(self) -> int:
        """Return the number of baseline lines covered by the original unit."""
        return self.old_end - self.old_start + 1

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {
            "old_start": self.old_start,
            "old_end": self.old_end,
            "new_start": self.new_start,
            "new_end": self.new_end,
        }
        if self.baseline_reference is not None:
            data["baseline_reference"] = self.baseline_reference.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> ReplacementUnitOrigin:
        """Deserialize from metadata dictionary."""
        baseline_metadata = data.get("baseline_reference")
        return cls(
            old_start=data["old_start"],
            old_end=data["old_end"],
            new_start=data["new_start"],
            new_end=data["new_end"],
            baseline_reference=(
                BaselineReference.from_dict(baseline_metadata, blob_contents)
                if baseline_metadata is not None else None
            ),
        )


@dataclass
class ReplacementUnit:
    """Explicit coupling between presence claims and absence claims.

    The deletion side references indexes in BatchOwnership.deletions so the
    canonical deletion constraint is stored only once in metadata.
    """

    presence_lines: list[str]
    deletion_indices: list[int]
    origin: ReplacementUnitOrigin | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        """Serialize to metadata dictionary."""
        data = {
            "presence_lines": self.presence_lines,
            "deletion_indices": self.deletion_indices,
        }
        if self.origin is not None:
            data["original_unit"] = self.origin.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: dict,
        blob_contents: dict[str, bytes] | None = None,
    ) -> ReplacementUnit:
        """Deserialize from metadata dictionary."""
        origin_metadata = data.get("original_unit")
        return cls(
            presence_lines=data.get("presence_lines", data.get("claimed_lines", [])),
            deletion_indices=data.get("deletion_indices", []),
            origin=(
                ReplacementUnitOrigin.from_dict(origin_metadata, blob_contents)
                if isinstance(origin_metadata, dict) else None
            ),
        )


def normalize_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Drop invalid references and coalesce overlapping replacement units."""
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
