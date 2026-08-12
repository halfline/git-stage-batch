"""Replacement edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from .baseline_anchor_matching import (
    BaselineRemovalEdit as _BaselineRemovalEdit,
    baseline_removal_edit as _baseline_removal_edit,
    replacement_origin_absence_bounds as _replacement_origin_absence_bounds,
)
from .baseline_edit_plan import BaselineEditPlan
from .baseline_replacement_choices import (
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
)
from .candidates import MergeResolution as _MergeResolution
from .validation import (
    ReplacementOldSideState as _ReplacementOldSideState,
    classify_replacement_old_side as _classify_replacement_old_side,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.sequence_equality import (
    line_slice_equals as _line_slice_matches,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.replacement_units import (
        ReplacementUnit,
        ReplacementUnitOrigin,
    )


def _record_mapped_replacement_lines(
    claimed_ranges: Sequence[tuple[int, ...]],
    mapping: LineMapping | None,
    mapped_target_lines: MappedRecordVector,
) -> bool | None:
    """Record a fully mapped unit; return None for mixed realization."""
    if mapping is None:
        return False

    original_count = len(mapped_target_lines)
    has_mapped_line = False
    has_missing_line = False
    for source_start, source_end in claimed_ranges:
        for source_line in range(source_start, source_end + 1):
            target_line = mapping.get_target_line_from_source_line(source_line)
            if target_line is None:
                has_missing_line = True
                continue
            has_mapped_line = True
            try:
                mapped_target_lines.append((target_line - 1,))
            except OverflowError:
                mapped_target_lines.truncate(original_count)
                return None
    if has_missing_line:
        mapped_target_lines.truncate(original_count)
        return None if has_mapped_line else False
    return has_mapped_line


def _deletion_target_position(
    claim: AbsenceClaim,
    mapping: LineMapping,
) -> int | None:
    """Return the target gap immediately after a mapped deletion anchor."""
    anchor_line = claim.anchor_line
    if anchor_line is None:
        return 0
    if type(anchor_line) is not int or anchor_line < 1:
        return None
    return mapping.get_target_line_from_source_line(anchor_line)


def _replacement_edit_with_origin_guard(
    claim: AbsenceClaim,
    origin: ReplacementUnitOrigin | None,
    working_lines: Sequence[bytes],
) -> _BaselineRemovalEdit | None:
    """Return a removal edit only if it fits inside the original parent unit."""
    removal_edit = _baseline_removal_edit(claim, working_lines)
    if removal_edit is None:
        return None

    if origin is None:
        return removal_edit

    parent_bounds = _replacement_origin_absence_bounds(origin, working_lines)
    if parent_bounds is None:
        return None

    start, end = removal_edit
    parent_start, parent_end = parent_bounds
    if start < parent_start or end > parent_end:
        return None
    return start, end


def _replacement_edit_from_parent_offset(
    claim: AbsenceClaim,
    origin: ReplacementUnitOrigin | None,
    claimed_ranges: Sequence[tuple[int, ...]],
    working_lines: Sequence[bytes],
) -> _BaselineRemovalEdit | None:
    """Place an equal-size split replacement by offset inside its parent."""
    if origin is None or not claim.content_lines:
        return None

    old_line_count = origin.old_line_count
    new_start = origin.new_start
    new_end = origin.new_end
    if (
        old_line_count <= 0
        or new_end < new_start
    ):
        return None

    new_line_count = new_end - new_start + 1
    if old_line_count != new_line_count:
        return None

    if len(claimed_ranges) != 1:
        return None

    first_claimed_line, last_claimed_line = claimed_ranges[0]
    claimed_line_count = last_claimed_line - first_claimed_line + 1

    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    if len(forbidden_sequence) != claimed_line_count:
        return None

    parent_bounds = _replacement_origin_absence_bounds(origin, working_lines)
    if parent_bounds is None:
        return None

    claim_reference = claim.baseline_reference
    origin_reference = origin.baseline_reference
    if (
        claim_reference is not None
        and claim_reference.has_after_line
        and origin_reference is not None
        and origin_reference.has_after_line
    ):
        relative_offset = (
            (claim_reference.after_line or 0)
            - (origin_reference.after_line or 0)
        )
    else:
        relative_offset = first_claimed_line - new_start

    if (
        relative_offset < 0
        or relative_offset + claimed_line_count > new_line_count
    ):
        return None

    parent_start, parent_end = parent_bounds
    start = parent_start + relative_offset
    end = start + len(forbidden_sequence)
    if start < parent_start or end > parent_end:
        return None
    if not _line_slice_matches(working_lines, start, forbidden_sequence):
        return None
    return start, end


def _replacement_edit_from_origin_resolution(
    claim: AbsenceClaim,
    unit_index: int,
    unit: ReplacementUnit,
    claimed_ranges: Sequence[tuple[int, ...]],
    working_lines: Sequence[bytes],
    resolution: _MergeResolution | None,
    *,
    max_results: int,
) -> _BaselineRemovalEdit | None:
    """Return a replacement edit from a reviewed origin-placement choice."""
    if resolution is None:
        return None

    key, choices = _replacement_origin_choices_for_unit(
        claim,
        unit_index,
        unit,
        ((source_start, source_end) for source_start, source_end in claimed_ranges),
        working_lines,
        max_results=max_results,
    )
    if key is None or key not in resolution.decisions:
        return None

    choice_index = resolution.decisions[key]
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    for choice in choices:
        if choice.choice_index == choice_index:
            return (
                choice.position,
                choice.position + len(forbidden_sequence),
            )

    raise _MergeError(_("Selected merge resolution is no longer valid"))


def _replacement_baseline_edit(
    claim: AbsenceClaim,
    unit_index: int,
    unit: ReplacementUnit,
    claimed_ranges: Sequence[tuple[int, ...]],
    working_lines: Sequence[bytes],
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
) -> tuple[_BaselineRemovalEdit, bool] | None:
    origin = getattr(unit, "origin", None)
    guarded_edit = _replacement_edit_with_origin_guard(
        claim,
        origin,
        working_lines,
    )
    if guarded_edit is not None:
        return guarded_edit, False

    offset_edit = _replacement_edit_from_parent_offset(
        claim,
        origin,
        claimed_ranges,
        working_lines,
    )
    if offset_edit is not None:
        return offset_edit, False

    reviewed_edit = _replacement_edit_from_origin_resolution(
        claim,
        unit_index,
        unit,
        claimed_ranges,
        working_lines,
        resolution,
        max_results=max_resolution_choices,
    )
    if reviewed_edit is None:
        return None
    return reviewed_edit, True


def plan_replacement_unit_edits(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    source_line_count: int,
    working_lines: Sequence[bytes],
    replacement_units: Sequence[ReplacementUnit],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    replacement_source_ranges: MappedRecordVector,
    mapped_replacement_target_lines: MappedRecordVector,
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
    source_to_working_mapping: LineMapping | None,
    spool_dir: str | Path | None,
) -> bool:
    """Plan coupled replacement units and record their claimed source ranges."""
    for unit_index, unit in enumerate(replacement_units):
        claimed_ranges = _collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        if claimed_ranges is None:
            return False
        try:
            if (
                not claimed_ranges
                or claimed_ranges[-1][1] > source_line_count
                or len(unit.deletion_indices) != 1
            ):
                return False

            deletion_index = unit.deletion_indices[0]
            if (
                type(deletion_index) is not int
                or deletion_index < 0
                or deletion_index >= len(deletion_claims)
            ):
                return False
            if deletion_edit_bounds[deletion_index][0]:
                return False

            claim = deletion_claims[deletion_index]
            replacement_is_mapped = _record_mapped_replacement_lines(
                claimed_ranges,
                source_to_working_mapping,
                mapped_replacement_target_lines,
            )
            if replacement_is_mapped is None:
                return False
            if replacement_is_mapped:
                assert source_to_working_mapping is not None
                old_side = _classify_replacement_old_side(
                    claim,
                    working_lines,
                    source_to_working_mapping,
                    claimed_ranges,
                    spool_dir=spool_dir,
                )
                if (
                    old_side is None
                    or old_side.state is _ReplacementOldSideState.PARTIAL
                ):
                    return False

                target_position = old_side.target_position
                if old_side.state is _ReplacementOldSideState.ABSENT:
                    target_position = _deletion_target_position(
                        claim,
                        source_to_working_mapping,
                    )
                if target_position is None:
                    return False

                target_end = target_position
                if old_side.state is _ReplacementOldSideState.FULL:
                    target_end += len(claim.content_lines)
                    plan.add_removal(target_position, target_end)
                for source_start, source_end in claimed_ranges:
                    replacement_source_ranges.append((source_start, source_end))
                deletion_edit_bounds[deletion_index] = (
                    1,
                    target_position,
                    target_end,
                    1,
                )
                continue

            replacement_edit = _replacement_baseline_edit(
                claim,
                unit_index,
                unit,
                claimed_ranges,
                working_lines,
                resolution,
                max_resolution_choices=max_resolution_choices,
            )
            if replacement_edit is None:
                return False

            removal_edit, coordinate_was_reviewed = replacement_edit
            start, end = removal_edit
            plan.add_source_ranges(
                start,
                end,
                (
                    (source_start, source_end)
                    for source_start, source_end in claimed_ranges
                ),
            )
            for source_start, source_end in claimed_ranges:
                replacement_source_ranges.append((source_start, source_end))
            deletion_edit_bounds[deletion_index] = (
                1,
                start,
                end,
                coordinate_was_reviewed,
            )
        finally:
            workspace.close_resource(claimed_ranges)

    return True


def replacement_source_ranges_fit_presence(
    presence_lines: LineRanges,
    replacement_source_ranges: MappedRecordVector,
) -> bool:
    """Sort replacement ranges and require disjoint presence coverage."""
    sort_mapped_records(replacement_source_ranges)
    presence_ranges = presence_lines.ranges()
    presence_range_index = 0
    previous_replacement_end = 0

    for source_start, source_end in replacement_source_ranges:
        if source_start < 1 or source_end < source_start:
            return False
        if source_start <= previous_replacement_end:
            return False
        while (
            presence_range_index < len(presence_ranges)
            and presence_ranges[presence_range_index][1] < source_start
        ):
            presence_range_index += 1
        if presence_range_index >= len(presence_ranges):
            return False
        presence_start, presence_end = presence_ranges[presence_range_index]
        if presence_start > source_start or source_end > presence_end:
            return False
        previous_replacement_end = source_end

    return True
