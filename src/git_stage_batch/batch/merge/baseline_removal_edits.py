"""Removal edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import (
    baseline_removal_edit,
    removal_boundary_context_matches_at,
    trusted_target_span_matches_working,
    unique_live_removal_edit,
)
from .baseline_edit_plan import BaselineEditPlan
from .validation import (
    ReplacementOldSideState,
    classify_replacement_old_side,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..line_matching.sequence_equality import line_slice_equals

if TYPE_CHECKING:
    from ...core.line_selection import LineSelection
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim


_SHIFTED_REMOVAL_CANDIDATE_LIMIT = 16


def plan_independent_removal_edits(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    *,
    selected_presence: LineSelection,
    source_to_working_mapping: LineMapping | None,
    mapped_source_lines: Sequence[tuple[int, ...]] | None,
    trusted_target_lines: Sequence[bytes] | None,
    trusted_target_to_working_mapping: LineMapping | None,
    allow_mapped_fallback: bool,
    spool_dir: str | Path | None,
) -> bool:
    """Plan removals not already coupled to replacement units.

    Prefer recorded offsets, then accept a uniquely identified shifted old
    side unchanged from the trusted target or one whose source mapping proves
    its exact structural gap. Shared storage-backed indexes and bounded live
    candidates avoid a file scan per claim.
    """
    mapped_claim_indices = workspace.record_vector(
        len(deletion_claims),
        "Q",
    )
    occurrence_index: LinePayloadOccurrenceIndex | None = None
    try:
        for deletion_index, claim in enumerate(deletion_claims):
            if deletion_edit_bounds[deletion_index][0]:
                continue

            coordinate_was_reviewed = False
            removal_edit = baseline_removal_edit(claim, working_lines)
            if (
                removal_edit is None
                and trusted_target_lines is not None
                and trusted_target_to_working_mapping is not None
            ):
                if occurrence_index is None:
                    occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        working_lines,
                    )
                removal_edit = unique_live_removal_edit(
                    claim,
                    working_lines,
                    occurrence_index,
                    candidate_limit=_SHIFTED_REMOVAL_CANDIDATE_LIMIT,
                )
                if (
                    removal_edit is not None
                    and trusted_target_span_matches_working(
                        working_lines,
                        trusted_target_lines,
                        trusted_target_to_working_mapping,
                        removal_edit[0],
                        removal_edit[1],
                    )
                ):
                    coordinate_was_reviewed = True
                else:
                    removal_edit = None
            if removal_edit is None:
                mapped_claim_indices.append((deletion_index,))
                continue

            start, end = removal_edit
            plan.add_removal(start, end)
            deletion_edit_bounds[deletion_index] = (
                1,
                start,
                end,
                int(coordinate_was_reviewed),
            )

        if not mapped_claim_indices:
            return True
        if (
            not allow_mapped_fallback
            or source_to_working_mapping is None
            or mapped_source_lines is None
        ):
            return False

        return _plan_mapped_independent_removal_edits(
            workspace,
            plan,
            working_lines,
            deletion_claims,
            deletion_edit_bounds,
            mapped_claim_indices,
            selected_presence,
            source_to_working_mapping,
            mapped_source_lines,
            spool_dir=spool_dir,
        )
    finally:
        workspace.close_resource(mapped_claim_indices)


def _plan_mapped_independent_removal_edits(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    deletion_indices: Sequence[tuple[int, ...]],
    selected_presence: LineSelection,
    source_to_working_mapping: LineMapping,
    mapped_source_lines: Sequence[tuple[int, ...]],
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Plan shifted removals only after proving their mapped gaps are disjoint."""
    source_line_count = len(source_to_working_mapping.source_to_target)
    next_unclaimed_targets = workspace.int_vector(source_line_count + 1)
    mapped_gaps = workspace.record_vector(len(deletion_indices), "QQQ")
    try:
        next_target_line = 0
        for source_index in range(source_line_count - 1, -1, -1):
            source_line = source_index + 1
            mapped_target_line = (
                source_to_working_mapping.source_to_target[source_index]
            )
            if (
                mapped_target_line != 0
                and source_line not in selected_presence
            ):
                next_target_line = mapped_target_line
            next_unclaimed_targets[source_index] = next_target_line

        for (deletion_index,) in deletion_indices:
            claim = deletion_claims[deletion_index]
            anchor_line = claim.anchor_line
            if anchor_line is None:
                start = 0
                next_source_index = 0
            elif (
                type(anchor_line) is not int
                or anchor_line < 1
                or anchor_line > source_line_count
            ):
                return False
            else:
                mapped_anchor = (
                    source_to_working_mapping.get_target_line_from_source_line(
                        anchor_line
                    )
                )
                if mapped_anchor is None:
                    return False
                start = mapped_anchor
                next_source_index = anchor_line

            following_target_line = next_unclaimed_targets[next_source_index]
            gap_end = (
                len(working_lines)
                if following_target_line == 0
                else following_target_line - 1
            )
            if gap_end < start:
                return False
            mapped_gaps.append((start, gap_end, deletion_index))

        sort_mapped_records(mapped_gaps)
        previous_gap_end = 0
        for start, gap_end, _deletion_index in mapped_gaps:
            if start < previous_gap_end:
                return False
            previous_gap_end = gap_end

        for gap_start, gap_end, deletion_index in mapped_gaps:
            claim = deletion_claims[deletion_index]
            if not removal_boundary_context_matches_at(
                claim,
                working_lines,
                gap_start,
                gap_end - gap_start,
            ):
                return False
            old_side = classify_replacement_old_side(
                claim,
                working_lines,
                source_to_working_mapping,
                selected_presence,
                spool_dir=spool_dir,
                mapped_source_lines=mapped_source_lines,
            )
            if (
                old_side is None
                or old_side.state is ReplacementOldSideState.PARTIAL
            ):
                return False
            removal_start = (
                gap_start
                if old_side.state is ReplacementOldSideState.ABSENT
                else old_side.target_position
            )
            if removal_start is None:
                return False
            removal_line_count = (
                len(claim.content_lines)
                if old_side.state is ReplacementOldSideState.FULL
                else 0
            )
            removal_end = removal_start + removal_line_count
            if (
                removal_start < gap_start
                or removal_end > gap_end
            ):
                return False

            plan.add_removal(removal_start, removal_end)
            deletion_edit_bounds[deletion_index] = (
                1,
                removal_start,
                removal_end,
                1,
            )
        return True
    finally:
        workspace.close_resource(mapped_gaps)
        workspace.close_resource(next_unclaimed_targets)


def all_deletions_are_already_absent(
    deletion_claims: Sequence[AbsenceClaim],
    working_lines: Sequence[bytes],
) -> bool:
    """Return whether baseline and source anchors prove removals satisfied."""
    for claim in deletion_claims:
        if not claim.content_lines:
            continue
        if baseline_removal_edit(claim, working_lines) is not None:
            return False

        forbidden_sequence = normalize_line_sequence_endings(
            claim.content_lines
        )
        if len(forbidden_sequence) > len(working_lines):
            continue

        anchor_line = claim.anchor_line
        if anchor_line is None:
            source_position = 0
        elif (
            type(anchor_line) is not int
            or anchor_line < 1
            or anchor_line > len(working_lines)
        ):
            return False
        else:
            source_position = anchor_line

        if line_slice_equals(
            working_lines,
            source_position,
            forbidden_sequence,
        ):
            return False

    return True
