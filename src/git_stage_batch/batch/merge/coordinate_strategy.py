"""Recorded-coordinate availability and merge-strategy choices."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .baseline_replacement_ranges import (
    collect_replacement_source_ranges,
    replacement_source_range_capacity,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.replacement_units import ReplacementUnit


AMBIGUITY_KEY = "baseline-coordinate-vs-structural"
_SOURCE_RANGE_RECORD_FORMAT = "QQ"


class CoordinateStrategyChoice(Enum):
    """A reviewed choice between two valid merge strategies."""

    STRUCTURAL = 1
    RECORDED_COORDINATES = 2


def has_recorded_baseline_coordinates(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether selected edit metadata includes a recorded coordinate."""
    for presence_claim in ownership.presence_claims:
        for claimed_line, presence_reference in (
            presence_claim.baseline_references.items()
        ):
            if (
                claimed_line in presence_line_set
                and presence_reference.has_after_line
            ):
                return True
    for deletion_claim in deletion_claims:
        deletion_reference = deletion_claim.baseline_reference
        if (
            deletion_reference is not None
            and deletion_reference.has_after_line
        ):
            return True
    for unit in ownership.replacement_units:
        origin = unit.origin
        if origin is None:
            continue
        origin_reference = origin.baseline_reference
        if origin_reference is not None and origin_reference.has_after_line:
            return True
    return False


def presence_lines_requiring_distinctive_context(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
    *,
    spool_dir: str | Path | None = None,
) -> LineRanges:
    """Return presence lines lacking coordinate or replacement anchoring."""
    source_selection = coerce_line_ranges(presence_line_set)
    if not source_selection:
        return LineRanges.empty()

    coverage_capacity = sum(
        len(claim.baseline_references)
        for claim in ownership.presence_claims
    ) + len(deletion_claims) + sum(
        replacement_source_range_capacity(unit.presence_lines)
        for unit in ownership.replacement_units
        if _unit_has_nonempty_deletion(unit, deletion_claims)
    )
    if coverage_capacity == 0:
        return source_selection

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        covered_ranges = workspace.record_vector(
            coverage_capacity,
            _SOURCE_RANGE_RECORD_FORMAT,
        )
        for claim in ownership.presence_claims:
            for claimed_line, reference in claim.baseline_references.items():
                if (
                    type(claimed_line) is int
                    and claimed_line > 0
                    and reference.has_after_line
                ):
                    covered_ranges.append((claimed_line, claimed_line))

        _append_legacy_replacement_coverage(
            covered_ranges,
            presence_line_set,
            deletion_claims,
        )

        for unit in ownership.replacement_units:
            if not _unit_has_nonempty_deletion(unit, deletion_claims):
                continue
            unit_ranges = collect_replacement_source_ranges(
                workspace,
                unit.presence_lines,
            )
            if unit_ranges is None:
                continue
            try:
                for source_start, source_end in unit_ranges:
                    covered_ranges.append((source_start, source_end))
            finally:
                workspace.close_resource(unit_ranges)

        if len(covered_ranges) > 1:
            sort_mapped_records(covered_ranges)
            _compact_source_ranges(covered_ranges)
        covered_selection = LineRanges.from_ranges(
            (start, end) for start, end in covered_ranges
        )
        return source_selection.difference(covered_selection)


def _append_legacy_replacement_coverage(
    covered_ranges: MappedRecordVector,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> None:
    """Cover legacy replacements coupled by their immediate source anchor."""
    selected_ranges = presence_line_set.ranges()
    for deletion in deletion_claims:
        if not deletion.content_lines:
            continue
        anchor_line = deletion.anchor_line
        if anchor_line is None:
            replacement_start = 1
        elif type(anchor_line) is int and anchor_line >= 0:
            replacement_start = anchor_line + 1
        else:
            continue
        for selected_start, selected_end in selected_ranges:
            if selected_start <= replacement_start <= selected_end:
                covered_ranges.append((replacement_start, selected_end))
                break
            if selected_start > replacement_start:
                break


def _unit_has_nonempty_deletion(
    unit: ReplacementUnit,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether a replacement unit has a usable deletion side."""
    return any(
        type(deletion_index) is int
        and 0 <= deletion_index < len(deletion_claims)
        and bool(deletion_claims[deletion_index].content_lines)
        for deletion_index in unit.deletion_indices
    )


def _compact_source_ranges(source_ranges: MappedRecordVector) -> None:
    """Coalesce ordered coverage ranges without rebuilding them on the heap."""
    retained_count = 0
    for source_start, source_end in source_ranges:
        if retained_count:
            previous_start, previous_end = source_ranges[retained_count - 1]
            if source_start <= previous_end + 1:
                source_ranges[retained_count - 1] = (
                    previous_start,
                    max(previous_end, source_end),
                )
                continue
        source_ranges[retained_count] = (source_start, source_end)
        retained_count += 1
    source_ranges.truncate(retained_count)
