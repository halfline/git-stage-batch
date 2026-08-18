"""Replacement-unit metadata normalization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Union

from ...core.line_selection import LineRanges
from ...core.coordinates import (
    LineBoundary,
    LineSpan,
    ReplacementNewSpace,
    ReplacementOldSpace,
)
from .claims import (
    format_ownership_line_set,
    parse_ownership_line_ranges,
)
from .metadata_types import (
    ReplacementUnitMetadata,
    ReplacementUnitOriginMetadata,
)
from .references import BaselineReference


@dataclass(init=False, frozen=True, slots=True)
class ReplacementUnitOrigin:
    """Original full replacement region for a selectable replacement sub-unit.

    Split replacement units may be smaller than the file-derived replacement run
    that created them. This context records that original run so merge/discard
    code can validate placement against the parent replacement boundary instead
    of treating the selected sub-unit as an unrelated edit.
    """

    _old_start_offset: int
    _old_end_offset: int
    _new_start_offset: int
    _new_end_offset: int
    baseline_reference: BaselineReference | None = None

    def __init__(
        self,
        old_start: int | None = None,
        old_end: int | None = None,
        new_start: int | None = None,
        new_end: int | None = None,
        baseline_reference: BaselineReference | None = None,
        *,
        old_span: object | None = None,
        new_span: object | None = None,
    ) -> None:
        if old_span is None:
            if old_start is None or old_end is None:
                raise ValueError("replacement origin requires an old span")
            if old_start <= 0 or old_end < old_start:
                raise ValueError("replacement origin has an invalid old span")
            old_start_offset, old_end_offset = old_start - 1, old_end
        elif old_start is not None or old_end is not None:
            raise ValueError("provide an old span or legacy old coordinates")
        else:
            if not isinstance(old_span, LineSpan):
                raise TypeError("replacement origin old span must be a LineSpan")
            old_start_offset = old_span.start.offset
            old_end_offset = old_span.end.offset
        if new_span is None:
            if new_start is None or new_end is None:
                raise ValueError("replacement origin requires a new span")
            if new_start <= 0 or new_end < new_start:
                raise ValueError("replacement origin has an invalid new span")
            new_start_offset, new_end_offset = new_start - 1, new_end
        elif new_start is not None or new_end is not None:
            raise ValueError("provide a new span or legacy new coordinates")
        else:
            if not isinstance(new_span, LineSpan):
                raise TypeError("replacement origin new span must be a LineSpan")
            new_start_offset = new_span.start.offset
            new_end_offset = new_span.end.offset
        object.__setattr__(self, "_old_start_offset", old_start_offset)
        object.__setattr__(self, "_old_end_offset", old_end_offset)
        object.__setattr__(self, "_new_start_offset", new_start_offset)
        object.__setattr__(self, "_new_end_offset", new_end_offset)
        object.__setattr__(self, "baseline_reference", baseline_reference)

    @property
    def old_span(self) -> LineSpan[ReplacementOldSpace]:
        """Return the typed old-side span as a computed adapter."""
        return LineSpan(
            LineBoundary(self._old_start_offset),
            LineBoundary(self._old_end_offset),
        )

    @property
    def new_span(self) -> LineSpan[ReplacementNewSpace]:
        """Return the typed new-side span as a computed adapter."""
        return LineSpan(
            LineBoundary(self._new_start_offset),
            LineBoundary(self._new_end_offset),
        )

    @property
    def old_start(self) -> int:
        return self._old_start_offset + 1

    @property
    def old_end(self) -> int:
        return self._old_end_offset

    @property
    def new_start(self) -> int:
        return self._new_start_offset + 1

    @property
    def new_end(self) -> int:
        return self._new_end_offset

    @property
    def old_line_count(self) -> int:
        """Return the number of baseline lines covered by the original unit."""
        return self._old_end_offset - self._old_start_offset

    def with_baseline_reference(
        self,
        baseline_reference: BaselineReference | None,
    ) -> ReplacementUnitOrigin:
        """Return the same compact geometry with new boundary evidence."""
        return ReplacementUnitOrigin(
            old_start=self.old_start,
            old_end=self.old_end,
            new_start=self.new_start,
            new_end=self.new_end,
            baseline_reference=baseline_reference,
        )

    def with_new_lines(
        self,
        new_start: int,
        new_end: int,
    ) -> ReplacementUnitOrigin:
        """Return the same origin rebound to a refreshed produced-side span."""
        return ReplacementUnitOrigin(
            old_start=self.old_start,
            old_end=self.old_end,
            new_start=new_start,
            new_end=new_end,
            baseline_reference=self.baseline_reference,
        )

    def to_dict(self) -> ReplacementUnitOriginMetadata:
        """Serialize to metadata dictionary."""
        data: ReplacementUnitOriginMetadata = {
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
        data: ReplacementUnitOriginMetadata,
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


@dataclass(frozen=True, slots=True)
class NoReplacementUnitOrigin:
    """A current unit that deliberately carries no parent provenance."""


@dataclass(frozen=True, slots=True)
class LegacyReplacementUnitOrigin:
    """Origin decoded from compatibility metadata, possibly absent or invalid."""

    value: ReplacementUnitOrigin | None


@dataclass(frozen=True, slots=True)
class ProvenReplacementUnitOrigin:
    """Validated current-schema replacement provenance."""

    value: ReplacementUnitOrigin

    def __post_init__(self) -> None:
        if _validated_replacement_unit_origin(self.value) is None:
            raise ValueError("current replacement provenance is malformed")


ReplacementUnitOriginEvidence = Union[
    NoReplacementUnitOrigin,
    LegacyReplacementUnitOrigin,
    ProvenReplacementUnitOrigin,
]


@dataclass(init=False)
class ReplacementUnit:
    """Explicit coupling between presence claims and absence claims.

    The deletion side references indexes in BatchOwnership.deletions so the
    canonical deletion constraint is stored only once in metadata.
    """

    presence_lines: Sequence[str | int]
    deletion_indices: list[int]
    origin_evidence: ReplacementUnitOriginEvidence = field(compare=False)

    def __init__(
        self,
        presence_lines: Sequence[str | int],
        deletion_indices: list[int],
        origin: ReplacementUnitOrigin | None = None,
        *,
        origin_evidence: ReplacementUnitOriginEvidence | None = None,
    ) -> None:
        if origin is not None and origin_evidence is not None:
            raise ValueError("provide origin or explicit origin evidence")
        self.presence_lines = presence_lines
        self.deletion_indices = deletion_indices
        self.origin_evidence = (
            origin_evidence
            if origin_evidence is not None
            else (
                ProvenReplacementUnitOrigin(origin)
                if origin is not None
                else NoReplacementUnitOrigin()
            )
        )

    @property
    def origin(self) -> ReplacementUnitOrigin | None:
        """Return only provenance that is valid enough for domain use."""
        if isinstance(self.origin_evidence, ProvenReplacementUnitOrigin):
            return self.origin_evidence.value
        if isinstance(self.origin_evidence, LegacyReplacementUnitOrigin):
            # Compatibility consumers still distinguish malformed legacy
            # evidence from an origin that was never recorded.  They must
            # validate it before use; normalization drops it.
            return self.origin_evidence.value
        return None

    def with_origin(self, origin: ReplacementUnitOrigin) -> ReplacementUnit:
        """Return this unit with a new origin in the same evidence tier."""
        evidence: ReplacementUnitOriginEvidence
        if isinstance(self.origin_evidence, ProvenReplacementUnitOrigin):
            evidence = ProvenReplacementUnitOrigin(origin)
        else:
            evidence = LegacyReplacementUnitOrigin(origin)
        return ReplacementUnit(
            self.presence_lines,
            self.deletion_indices,
            origin_evidence=evidence,
        )

    def to_dict(self) -> ReplacementUnitMetadata:
        """Serialize to metadata dictionary."""
        data: ReplacementUnitMetadata = {
            "presence_lines": list(self.presence_lines),
            "deletion_indices": self.deletion_indices,
        }
        if self.origin is not None:
            data["original_unit"] = self.origin.to_dict()
        return data

    @classmethod
    def from_dict(
        cls,
        data: ReplacementUnitMetadata,
        blob_contents: dict[str, bytes] | None = None,
    ) -> ReplacementUnit:
        """Deserialize from metadata dictionary."""
        origin_metadata = data.get("original_unit")
        legacy_origin: ReplacementUnitOrigin | None = None
        if isinstance(origin_metadata, dict):
            try:
                legacy_origin = ReplacementUnitOrigin.from_dict(
                    origin_metadata,
                    blob_contents,
                )
            except (KeyError, TypeError, ValueError):
                # Compatibility metadata may predate canonical span checks.
                # It remains explicitly legacy and cannot grant provenance.
                legacy_origin = None
        return cls(
            presence_lines=data.get("presence_lines", data.get("claimed_lines", [])),
            deletion_indices=data.get("deletion_indices", []),
            origin_evidence=LegacyReplacementUnitOrigin(legacy_origin),
        )


def replacement_counts_cover_origin(
    origin: ReplacementUnitOrigin | None,
    presence_line_count: int,
    deletion_line_count: int,
) -> bool:
    """Return whether selected old/new counts cover a complete origin."""
    if not isinstance(origin, ReplacementUnitOrigin):
        return False
    if (
        type(origin.old_start) is not int
        or type(origin.old_end) is not int
        or type(origin.new_start) is not int
        or type(origin.new_end) is not int
        or origin.old_start < 1
        or origin.new_start < 1
        or origin.old_end < origin.old_start
        or origin.new_end < origin.new_start
    ):
        return False
    return (
        presence_line_count == origin.new_end - origin.new_start + 1
        and deletion_line_count == origin.old_end - origin.old_start + 1
    )


def normalize_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Drop invalid references and coalesce overlapping replacement units."""
    normalized_units: list[
        tuple[LineRanges, tuple[int, ...], ReplacementUnitOriginEvidence]
    ] = []
    for unit in replacement_units:
        claimed = parse_ownership_line_ranges(unit.presence_lines)
        deletion_indices = tuple(
            sorted(
                {
                    index
                    for index in unit.deletion_indices
                    if type(index) is int and 0 <= index < deletion_count
                }
            )
        )
        if not claimed or not deletion_indices:
            continue
        normalized_units.append(
            (
                claimed,
                deletion_indices,
                _normalize_origin_evidence(unit.origin_evidence),
            )
        )

    if not normalized_units:
        return []

    components = _replacement_unit_components(normalized_units)
    result: list[ReplacementUnit] = []
    for component_indices in components:
        claimed = LineRanges.from_ranges(
            source_range
            for unit_index in component_indices
            for source_range in normalized_units[unit_index][0].ranges()
        )
        component_deletion_indices = sorted(
            {
                deletion_index
                for unit_index in component_indices
                for deletion_index in normalized_units[unit_index][1]
            }
        )
        remaining_indices = iter(component_indices)
        first_index = next(remaining_indices)
        origin_evidence = normalized_units[first_index][2]
        for unit_index in remaining_indices:
            origin_evidence = _merge_replacement_unit_origins(
                origin_evidence,
                normalized_units[unit_index][2],
            )
        result.append(
            ReplacementUnit(
                presence_lines=format_ownership_line_set(claimed),
                deletion_indices=component_deletion_indices,
                origin_evidence=origin_evidence,
            )
        )
    return result


def _replacement_unit_components(
    normalized_units: list[
        tuple[LineRanges, tuple[int, ...], ReplacementUnitOriginEvidence]
    ],
) -> list[list[int]]:
    """Return overlap components without comparing every pair of units."""
    parents = list(range(len(normalized_units)))
    ranks = [0] * len(normalized_units)

    def find(unit_index: int) -> int:
        while parents[unit_index] != unit_index:
            parents[unit_index] = parents[parents[unit_index]]
            unit_index = parents[unit_index]
        return unit_index

    def union(left_index: int, right_index: int) -> None:
        left_root = find(left_index)
        right_root = find(right_index)
        if left_root == right_root:
            return
        if ranks[left_root] < ranks[right_root]:
            left_root, right_root = right_root, left_root
        parents[right_root] = left_root
        if ranks[left_root] == ranks[right_root]:
            ranks[left_root] += 1

    deletion_owner: dict[int, int] = {}
    for unit_index, (_claimed, deletion_indices, _origin) in enumerate(
        normalized_units
    ):
        for deletion_index in deletion_indices:
            owner = deletion_owner.setdefault(deletion_index, unit_index)
            union(owner, unit_index)

    intervals = sorted(
        (start, end, unit_index)
        for unit_index, (claimed, _deletions, _origin) in enumerate(
            normalized_units
        )
        for start, end in claimed.ranges()
    )
    active_end = 0
    active_unit_index: int | None = None
    for start, end, unit_index in intervals:
        if active_unit_index is None or start > active_end:
            active_end = end
            active_unit_index = unit_index
            continue
        union(active_unit_index, unit_index)
        if end > active_end:
            active_end = end
            active_unit_index = unit_index

    members_by_root: dict[int, list[int]] = {}
    for unit_index in range(len(normalized_units)):
        members_by_root.setdefault(find(unit_index), []).append(unit_index)

    return sorted(members_by_root.values(), key=lambda members: members[0])


def _validated_replacement_unit_origin(
    origin: ReplacementUnitOrigin | None,
) -> ReplacementUnitOrigin | None:
    """Return a well-formed origin without granting malformed evidence authority."""
    if not isinstance(origin, ReplacementUnitOrigin):
        return None
    if (
        type(origin.old_start) is not int
        or type(origin.old_end) is not int
        or type(origin.new_start) is not int
        or type(origin.new_end) is not int
        or origin.old_start < 1
        or origin.new_start < 1
        or origin.old_start > origin.old_end
        or origin.new_start > origin.new_end
    ):
        return None
    return origin


def _normalize_origin_evidence(
    evidence: ReplacementUnitOriginEvidence,
) -> ReplacementUnitOriginEvidence:
    if isinstance(evidence, ProvenReplacementUnitOrigin):
        return evidence
    if isinstance(evidence, LegacyReplacementUnitOrigin):
        return LegacyReplacementUnitOrigin(
            _validated_replacement_unit_origin(evidence.value)
        )
    return NoReplacementUnitOrigin()


def _origin_value(
    evidence: ReplacementUnitOriginEvidence,
) -> ReplacementUnitOrigin | None:
    if isinstance(evidence, ProvenReplacementUnitOrigin):
        return evidence.value
    if isinstance(evidence, LegacyReplacementUnitOrigin):
        return evidence.value
    return None


def _merge_replacement_unit_origins(
    left: ReplacementUnitOriginEvidence,
    right: ReplacementUnitOriginEvidence,
) -> ReplacementUnitOriginEvidence:
    """Fail closed when current coalesced units disagree on provenance."""
    left_value = _origin_value(left)
    right_value = _origin_value(right)
    if left_value == right_value:
        if isinstance(left, ProvenReplacementUnitOrigin):
            return left
        if isinstance(right, ProvenReplacementUnitOrigin):
            return right
        return left
    if left_value is None:
        return right
    if right_value is None:
        return left
    if isinstance(
        left,
        ProvenReplacementUnitOrigin,
    ) or isinstance(right, ProvenReplacementUnitOrigin):
        raise ValueError("current replacement units disagree on parent provenance")
    return LegacyReplacementUnitOrigin(None)
