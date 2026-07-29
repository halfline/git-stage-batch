"""Removal edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ...core.mapped_storage import MappedRecordVector
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import baseline_removal_edit
from .baseline_edit_plan import BaselineEditPlan
from ..line_matching.sequence_equality import line_slice_equals

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim


def plan_independent_removal_edits(
    plan: BaselineEditPlan,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
) -> bool:
    """Plan removals not already coupled to replacement units."""
    for deletion_index, claim in enumerate(deletion_claims):
        if deletion_edit_bounds[deletion_index][0]:
            continue

        removal_edit = baseline_removal_edit(claim, working_lines)
        if removal_edit is None:
            return False

        start, end = removal_edit
        plan.add_removal(start, end)
        deletion_edit_bounds[deletion_index] = (
            1,
            start,
            end,
            0,
        )

    return True


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
