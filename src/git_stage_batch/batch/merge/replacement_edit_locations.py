"""Resolve replacement removal locations from origin and trusted evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
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
from .candidates import MergeResolution as _MergeResolution
from ..line_matching.sequence_equality import line_slice_equals as _line_slice_matches
from ..ownership.replacement_units import (
    replacement_counts_cover_origin as _replacement_counts_cover_origin,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.replacement_units import ReplacementUnit, ReplacementUnitOrigin


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
    if old_line_count <= 0 or new_end < new_start:
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
        relative_offset = (claim_reference.after_line or 0) - (
            origin_reference.after_line or 0
        )
    else:
        relative_offset = first_claimed_line - new_start

    if relative_offset < 0 or relative_offset + claimed_line_count > new_line_count:
        return None

    parent_start, parent_end = parent_bounds
    start = parent_start + relative_offset
    end = start + len(forbidden_sequence)
    if start < parent_start or end > parent_end:
        return None
    if not _line_slice_matches(working_lines, start, forbidden_sequence):
        return None
    return start, end


def _replacement_edit_from_trusted_target(
    claim: AbsenceClaim,
    origin: ReplacementUnitOrigin | None,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_line_count: int,
    source_lines: Sequence[bytes] | None,
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    source_to_working_mapping: LineMapping | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
    *,
    allow_mapped_source_predecessor: bool,
) -> _BaselineRemovalEdit | None:
    """Return a complete replacement span unchanged from a trusted target.

    A later committed or staged edit can transform the historical old side of
    a replacement without changing the source boundaries around it.  Accept
    that transformed span only when the complete parent replacement is
    selected and every target line between those boundaries maps unchanged
    from the trusted target.
    """
    if (
        origin is None
        or claim.baseline_reference != origin.baseline_reference
        or trusted_target_lines is None
        or source_to_working_mapping is None
        or source_to_trusted_target_mapping is None
        or trusted_target_to_working_mapping is None
        or len(claimed_ranges) != 1
    ):
        return None

    source_start, source_end = claimed_ranges[0]
    selected_line_count = source_end - source_start + 1
    if (
        source_start <= 1
        or source_end >= source_line_count
        or not _replacement_counts_cover_origin(
            origin,
            selected_line_count,
            len(claim.content_lines),
        )
    ):
        return None

    before_source_line = source_start - 1
    after_source_line = source_end + 1
    working_before = source_to_working_mapping.get_target_line_from_source_line(
        before_source_line
    )
    working_after = source_to_working_mapping.get_target_line_from_source_line(
        after_source_line
    )
    trusted_before = source_to_trusted_target_mapping.get_target_line_from_source_line(
        before_source_line
    )
    if (
        working_before is None
        or trusted_before is None
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_before
        )
        != working_before
    ):
        return None

    trusted_after = source_to_trusted_target_mapping.get_target_line_from_source_line(
        after_source_line
    )
    if trusted_after is None:
        if not allow_mapped_source_predecessor:
            return None
        return _replacement_edit_from_mapped_source_predecessor(
            claim,
            origin,
            source_start,
            source_end,
            source_line_count,
            source_lines,
            working_lines,
            working_before,
            trusted_before,
            source_to_working_mapping,
            source_to_trusted_target_mapping,
            trusted_target_to_working_mapping,
            trusted_target_lines,
        )
    if working_after is None:
        return None

    working_start = working_before
    working_end = working_after - 1
    trusted_start = trusted_before
    trusted_end = trusted_after - 1
    if (
        working_end <= working_start
        or trusted_end - trusted_start != working_end - working_start
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_after
        )
        != working_after
    ):
        return None

    for offset in range(trusted_end - trusted_start):
        trusted_line = trusted_start + offset + 1
        working_line = working_start + offset + 1
        if (
            trusted_target_to_working_mapping.get_target_line_from_source_line(
                trusted_line
            )
            != working_line
            or trusted_target_lines[trusted_line - 1] != working_lines[working_line - 1]
        ):
            return None
    return working_start, working_end


def _replacement_edit_from_mapped_source_predecessor(
    claim: AbsenceClaim,
    origin: ReplacementUnitOrigin,
    source_start: int,
    source_end: int,
    source_line_count: int,
    source_lines: Sequence[bytes] | None,
    working_lines: Sequence[bytes],
    working_before: int,
    trusted_before: int,
    source_to_working_mapping: LineMapping,
    source_to_trusted_target_mapping: LineMapping,
    trusted_target_to_working_mapping: LineMapping,
    trusted_target_lines: Sequence[bytes],
) -> _BaselineRemovalEdit | None:
    """Return an exact live predecessor retained after one complete new side.

    Source advancement can retain a committed predecessor immediately after a
    newer, fully owned replacement.  The predecessor may have a different line
    count from the historical old side, so the selected range's immediate
    following line no longer identifies the trusted boundary.  Accept that
    layout only for an unshifted complete origin.  The selected side must map an
    initial prefix, the complete contiguous predecessor must equal the live
    gap, and its remaining suffix must provide the rest of that gap.  The
    surrounding source lines must still map through the trusted target, and
    that target gap must contain the exact historical old side.
    """
    if (
        source_lines is None
        or source_start != origin.new_start
        or source_end != origin.new_end
    ):
        return None
    predecessor_start = source_end + 1
    for source_line in range(predecessor_start, source_line_count + 1):
        trusted_line = (
            source_to_trusted_target_mapping.get_target_line_from_source_line(
                source_line
            )
        )
        working_line = source_to_working_mapping.get_target_line_from_source_line(
            source_line
        )
        if trusted_line is not None:
            if working_line is None:
                return None
            working_end = working_line - 1
            if (
                source_line == predecessor_start
                or working_end <= working_before
                or trusted_target_to_working_mapping.get_target_line_from_source_line(
                    trusted_line
                )
                != working_line
            ):
                return None
            historical_old_side = normalize_line_sequence_endings(claim.content_lines)
            if trusted_line - trusted_before - 1 != len(
                historical_old_side
            ) or not _line_slice_matches(
                trusted_target_lines,
                trusted_before,
                historical_old_side,
            ):
                return None

            predecessor_line_count = source_line - predecessor_start
            if predecessor_line_count != working_line - working_before - 1:
                return None
            normalized_source = normalize_line_sequence_endings(source_lines)
            normalized_working = normalize_line_sequence_endings(working_lines)
            for offset in range(predecessor_line_count):
                if (
                    normalized_source[predecessor_start - 1 + offset]
                    != normalized_working[working_before + offset]
                ):
                    return None

            next_target_line = working_before + 1
            has_mapped_selected_line = False
            has_missing_selected_line = False
            for selected_source_line in range(source_start, source_end + 1):
                target_line = (
                    source_to_working_mapping.get_target_line_from_source_line(
                        selected_source_line
                    )
                )
                if target_line is None:
                    has_missing_selected_line = True
                    continue
                if (
                    has_missing_selected_line
                    or target_line != next_target_line
                    or source_to_working_mapping.get_source_line_from_target_line(
                        target_line
                    )
                    != selected_source_line
                ):
                    return None
                has_mapped_selected_line = True
                next_target_line += 1

            has_missing_predecessor_line = False
            has_mapped_predecessor_line = False
            for predecessor_source_line in range(predecessor_start, source_line):
                target_line = (
                    source_to_working_mapping.get_target_line_from_source_line(
                        predecessor_source_line
                    )
                )
                if target_line is None:
                    if has_mapped_predecessor_line:
                        return None
                    has_missing_predecessor_line = True
                    continue
                if (
                    target_line != next_target_line
                    or source_to_working_mapping.get_source_line_from_target_line(
                        target_line
                    )
                    != predecessor_source_line
                ):
                    return None
                has_mapped_predecessor_line = True
                next_target_line += 1
            if not (
                has_mapped_selected_line
                and has_missing_selected_line
                and has_missing_predecessor_line
                and has_mapped_predecessor_line
                and next_target_line == working_line
            ):
                return None
            return working_before, working_end
    return None


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


def _plan_partial_replacement_from_origin_resolution(
    plan: BaselineEditPlan,
    claim: AbsenceClaim,
    unit_index: int,
    unit: ReplacementUnit,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_line_count: int,
    mapping: LineMapping | None,
    working_lines: Sequence[bytes],
    resolution: _MergeResolution | None,
    *,
    max_results: int,
) -> _BaselineRemovalEdit | None:
    """Move one reviewed split child to its mapped source-order boundary."""
    origin = unit.origin
    if (
        origin is None
        or resolution is None
        or mapping is None
        or len(claimed_ranges) != 1
    ):
        return None
    source_start, source_end = claimed_ranges[0]
    selected_line_count = source_end - source_start + 1
    if (
        source_start <= 1
        or source_end >= source_line_count
        or _replacement_counts_cover_origin(
            origin,
            selected_line_count,
            len(claim.content_lines),
        )
    ):
        return None

    removal_edit = _replacement_edit_from_origin_resolution(
        claim,
        unit_index,
        unit,
        claimed_ranges,
        working_lines,
        resolution,
        max_results=max_results,
    )
    if removal_edit is None:
        return None

    previous_target = mapping.get_target_line_from_source_line(source_start - 1)
    next_target = mapping.get_target_line_from_source_line(source_end + 1)
    if previous_target is None or next_target is None:
        return None
    insertion_start = previous_target
    insertion_end = next_target - 1
    removal_start, removal_end = removal_edit
    source_ranges = (
        (source_range_start, source_range_end)
        for source_range_start, source_range_end in claimed_ranges
    )
    if insertion_start == insertion_end:
        plan.add_removal(removal_start, removal_end)
        plan.add_source_ranges(
            insertion_start,
            insertion_start,
            source_ranges,
        )
    elif insertion_start == removal_start and insertion_end == removal_end:
        plan.add_source_ranges(
            removal_start,
            removal_end,
            source_ranges,
        )
    else:
        return None
    return removal_edit


def _replacement_baseline_edit(
    claim: AbsenceClaim,
    unit_index: int,
    unit: ReplacementUnit,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_line_count: int,
    source_lines: Sequence[bytes] | None,
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    source_to_working_mapping: LineMapping | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
    allow_mapped_source_predecessor: bool,
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

    trusted_edit = _replacement_edit_from_trusted_target(
        claim,
        origin,
        claimed_ranges,
        source_line_count,
        source_lines,
        working_lines,
        trusted_target_lines,
        source_to_working_mapping,
        source_to_trusted_target_mapping,
        trusted_target_to_working_mapping,
        allow_mapped_source_predecessor=allow_mapped_source_predecessor,
    )
    if trusted_edit is not None:
        return trusted_edit, True

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
