"""Replay deletions while retaining lines added to the batch source."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.coordinates import (
    BatchSourceSpace,
    LineBoundary,
    LineSpan,
    WorktreeSpace,
)
from ...core.mapped_storage import (
    MappedIntVector,
    MappedRecordVector,
    sort_mapped_records,
)
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import removal_boundary_context_matches_at
from .baseline_edit_plan import BaselineEditPlan
from .validation import (
    ReplacementOldSideState,
    classify_replacement_old_side,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import normalized_line_payload
from ..line_matching.sequence_equality import line_slice_equals

if TYPE_CHECKING:
    from ...core.line_selection import LineSelection
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim


@dataclass(frozen=True, slots=True)
class _DeletionRegion:
    """Source lines to retain and their surrounding target region."""

    source: LineSpan[BatchSourceSpace]
    target: LineSpan[WorktreeSpace]


def deletion_may_cross_source_insertion(
    source_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether a deletion may cross lines added to its source."""
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if (
            not claim.content_lines
            or reference is None
            or not reference.has_after_line
            or not reference.has_before_line
        ):
            continue
        baseline_start = reference.after_line or 0
        if reference.before_line is not None and reference.before_line != (
            baseline_start + len(claim.content_lines) + 1
        ):
            continue

        source_start = claim.anchor.offset
        if source_start > len(source_lines):
            continue
        normalized_content = normalize_line_sequence_endings(claim.content_lines)
        if line_slice_equals(source_lines, source_start, normalized_content):
            continue
        if reference.before_line is None:
            if source_start < len(source_lines):
                return True
            continue
        if (
            reference.before_content is not None
            and source_start < len(source_lines)
            and normalized_line_payload(source_lines[source_start])
            != normalized_line_payload(reference.before_content)
        ):
            return True
    return False


def plan_deletions_across_source_insertions(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    deletion_indices: Sequence[tuple[int, ...]],
    planned_claims: MappedIntVector,
    selected_presence: LineSelection,
    source_to_working_mapping: LineMapping,
    mapped_source_lines: Sequence[tuple[int, ...]],
    *,
    spool_dir: str | Path | None,
) -> None:
    """Plan safe removals around lines that remain from the batch source."""
    ordered_claims = workspace.record_vector(len(deletion_indices), "QQ")
    candidate_regions = workspace.record_vector(len(deletion_indices), "QQQ")
    try:
        for (deletion_index,) in deletion_indices:
            ordered_claims.append(
                (deletion_claims[deletion_index].anchor.offset, deletion_index)
            )
        sort_mapped_records(ordered_claims)

        previous_source_end = 0
        for source_start, deletion_index in ordered_claims:
            if source_start < previous_source_end:
                continue
            source_span = _inserted_source_span(
                deletion_claims[deletion_index],
                source_lines,
            )
            if source_span is None:
                continue
            previous_source_end = source_span.end.offset
            if all(
                source_line in selected_presence
                for source_line in range(
                    source_span.start.offset + 1,
                    source_span.end.offset + 1,
                )
            ):
                continue
            candidate_regions.append(
                (
                    source_span.start.offset,
                    source_span.end.offset,
                    deletion_index,
                )
            )

        for source_start, source_end, deletion_index in candidate_regions:
            candidate_source_span: LineSpan[BatchSourceSpace] = LineSpan(
                LineBoundary(source_start),
                LineBoundary(source_end),
            )
            region = _map_deletion_region(
                candidate_source_span,
                source_line_count=len(source_lines),
                working_line_count=len(working_lines),
                mapping=source_to_working_mapping,
            )
            if region is None or not _region_contains_only_source_lines(
                region,
                source_to_working_mapping,
            ):
                continue

            claim = deletion_claims[deletion_index]
            target_start = region.target.start.offset
            target_end = region.target.end.offset
            if not removal_boundary_context_matches_at(
                claim,
                working_lines,
                target_start,
                target_end - target_start,
            ):
                continue

            old_side = classify_replacement_old_side(
                claim,
                working_lines,
                source_to_working_mapping,
                selected_presence,
                spool_dir=spool_dir,
                mapped_source_lines=mapped_source_lines,
                additional_claimed_span=candidate_source_span,
            )
            if old_side is None:
                continue
            if old_side.state is ReplacementOldSideState.FULL:
                if old_side.target_position is None:
                    continue
                removal_start = old_side.target_position
                removal_end = removal_start + len(claim.content_lines)
                plan.add_removal(removal_start, removal_end)
                actual_start = removal_start
                actual_end = removal_end
            elif old_side.state is ReplacementOldSideState.PARTIAL:
                if not _append_unmapped_removals(
                    plan,
                    region,
                    source_to_working_mapping,
                ):
                    continue
                actual_start = target_start
                actual_end = target_end
            elif old_side.state in (
                ReplacementOldSideState.FULLY_CLAIMED,
                ReplacementOldSideState.ABSENT,
            ):
                actual_start = target_start
                actual_end = target_start
            else:
                continue

            planned_claims[deletion_index] = 1
            deletion_edit_bounds[deletion_index] = (
                1,
                actual_start,
                actual_end,
                1,
            )
    finally:
        workspace.close_resource(candidate_regions)
        workspace.close_resource(ordered_claims)


def _inserted_source_span(
    claim: AbsenceClaim,
    source_lines: Sequence[bytes],
) -> LineSpan[BatchSourceSpace] | None:
    """Locate source lines between a deletion's saved boundaries."""
    reference = claim.baseline_reference
    if (
        not claim.content_lines
        or reference is None
        or not reference.has_after_line
        or not reference.has_before_line
    ):
        return None

    baseline_start = reference.after_line or 0
    if reference.before_line is not None and reference.before_line != (
        baseline_start + len(claim.content_lines) + 1
    ):
        return None

    source_start = claim.anchor.offset
    if source_start > len(source_lines):
        return None
    if reference.after_line is None:
        if source_start != 0:
            return None
    elif (
        source_start == 0
        or reference.after_content is None
        or normalized_line_payload(source_lines[source_start - 1])
        != normalized_line_payload(reference.after_content)
    ):
        return None

    if reference.before_line is None:
        source_end = len(source_lines)
    else:
        if reference.before_content is None:
            return None
        expected_before = normalized_line_payload(reference.before_content)
        source_end = source_start
        while source_end < len(source_lines) and (
            normalized_line_payload(source_lines[source_end]) != expected_before
        ):
            source_end += 1
        if source_end == len(source_lines):
            return None

    if source_end == source_start:
        return None
    if len(claim.content_lines) == source_end - source_start and line_slice_equals(
        source_lines,
        source_start,
        normalize_line_sequence_endings(claim.content_lines),
    ):
        return None
    return LineSpan(
        LineBoundary(source_start),
        LineBoundary(source_end),
    )


def _map_deletion_region(
    source_span: LineSpan[BatchSourceSpace],
    *,
    source_line_count: int,
    working_line_count: int,
    mapping: LineMapping,
) -> _DeletionRegion | None:
    """Map a source span and both of its outer boundaries."""
    source_start = source_span.start.offset
    source_end = source_span.end.offset
    if source_start == 0:
        target_start = 0
    else:
        mapped_start = mapping.get_target_line_from_source_line(source_start)
        if mapped_start is None:
            return None
        target_start = mapped_start

    if source_end == source_line_count:
        target_end = working_line_count
    else:
        mapped_after = mapping.get_target_line_from_source_line(source_end + 1)
        if mapped_after is None:
            return None
        target_end = mapped_after - 1
    if target_end < target_start:
        return None

    previous_target = target_start
    for source_line in range(source_start + 1, source_end + 1):
        target_line = mapping.get_target_line_from_source_line(source_line)
        if (
            target_line is None
            or target_line <= previous_target
            or target_line > target_end
        ):
            return None
        previous_target = target_line

    return _DeletionRegion(
        source=source_span,
        target=LineSpan(
            LineBoundary(target_start),
            LineBoundary(target_end),
        ),
    )


def _region_contains_only_source_lines(
    region: _DeletionRegion,
    mapping: LineMapping,
) -> bool:
    """Return whether mapped target lines belong to this source span."""
    source_start = region.source.start.offset
    source_end = region.source.end.offset
    for target_index in range(
        region.target.start.offset,
        region.target.end.offset,
    ):
        source_line = mapping.get_source_line_from_target_line(target_index + 1)
        if source_line is not None and not (source_start < source_line <= source_end):
            return False
    return True


def _append_unmapped_removals(
    plan: BaselineEditPlan,
    region: _DeletionRegion,
    mapping: LineMapping,
) -> bool:
    """Remove target-only runs while retaining mapped source lines."""
    pending_start: int | None = None
    removed_any = False
    target_end = region.target.end.offset
    for target_index in range(region.target.start.offset, target_end):
        if mapping.get_source_line_from_target_line(target_index + 1) is None:
            if pending_start is None:
                pending_start = target_index
            continue
        if pending_start is not None:
            plan.add_removal(pending_start, target_index)
            pending_start = None
            removed_any = True
    if pending_start is not None:
        plan.add_removal(pending_start, target_end)
        removed_any = True
    return removed_any
