"""Replacement edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from .baseline_anchor_matching import (
    BaselineRemovalEdit as _BaselineRemovalEdit,
    baseline_removal_edit as _baseline_removal_edit,
    replacement_origin_absence_bounds as _replacement_origin_absence_bounds,
    trusted_target_span_matches_working as _trusted_target_span_matches_working,
    unique_live_removal_context_bounds as _unique_live_removal_context_bounds,
    unique_live_removal_edit as _unique_live_removal_edit,
)
from .baseline_edit_plan import BaselineEditPlan
from .baseline_replacement_choices import (
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    replacement_source_range_capacity as _replacement_source_range_capacity,
)
from .candidates import MergeResolution as _MergeResolution
from .validation import (
    ReplacementOldSideState as _ReplacementOldSideState,
    build_mapped_source_line_index as _build_mapped_source_line_index,
    classify_replacement_old_side as _classify_replacement_old_side,
    complete_unrealized_replacement_group_target_bounds as _complete_group_bounds,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..line_matching.sequence_equality import (
    line_slice_equals as _line_slice_matches,
)
from ..ownership.replacement_units import (
    replacement_counts_cover_origin as _replacement_counts_cover_origin,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
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


def _mixed_mapped_replacement_bounds(
    claimed_ranges: Sequence[tuple[int, ...]],
    mapping: LineMapping,
    *,
    minimum_target_start: int,
) -> tuple[int, int] | None:
    """Return the target island bounded by one partly mapped source range.

    This proof is reserved for rebuilding saved batch content from its trusted
    predecessor.  Both immediate source neighbors must map around the island,
    every mapped claimed line must stay ordered inside it, and no target line
    in the island may map to source content outside the claimed range.
    """
    if len(claimed_ranges) != 1:
        return None
    source_start, source_end = claimed_ranges[0]
    if source_start <= 1 or source_end >= len(mapping.source_to_target):
        return None

    previous_target = mapping.get_target_line_from_source_line(source_start - 1)
    next_target = mapping.get_target_line_from_source_line(source_end + 1)
    if previous_target is None or next_target is None or next_target <= previous_target:
        return None

    target_start = previous_target
    target_end = next_target - 1
    if target_start < minimum_target_start:
        return None
    previous_mapped_index = target_start - 1
    for source_line in range(source_start, source_end + 1):
        target_line = mapping.get_target_line_from_source_line(source_line)
        if target_line is None:
            continue
        target_index = target_line - 1
        if (
            target_index < target_start
            or target_index >= target_end
            or target_index <= previous_mapped_index
        ):
            return None
        previous_mapped_index = target_index

    for target_line in range(target_start + 1, target_end + 1):
        mapped_source_line = mapping.get_source_line_from_target_line(target_line)
        if mapped_source_line is not None and not (
            source_start <= mapped_source_line <= source_end
        ):
            return None
    return target_start, target_end


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


@dataclass(slots=True)
class _TrustedPartialReplacementContext:
    """Parent-scoped verification shared by split replacement children."""

    working_parent_start: int
    working_parent_end: int
    parent_occurrences: LinePayloadOccurrenceIndex | None = None

    def close(self) -> None:
        """Release the parent-scoped occurrence index, if one was needed."""
        if self.parent_occurrences is None:
            return
        self.parent_occurrences.close()
        self.parent_occurrences = None


def _trusted_partial_replacement_context(
    workspace: MatcherWorkspace,
    origin: ReplacementUnitOrigin | None,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    source_to_working_mapping: LineMapping | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
) -> _TrustedPartialReplacementContext | None:
    """Verify one trusted parent span once for all of its split children."""
    if (
        origin is None
        or origin.baseline_reference is None
        or trusted_target_lines is None
        or source_to_working_mapping is None
        or source_to_trusted_target_mapping is None
        or trusted_target_to_working_mapping is None
    ):
        return None

    before_source_line = origin.new_start - 1
    after_source_line = origin.new_end + 1
    if before_source_line < 1 or after_source_line > len(source_lines):
        return None

    working_before = source_to_working_mapping.get_target_line_from_source_line(
        before_source_line
    )
    working_after = source_to_working_mapping.get_target_line_from_source_line(
        after_source_line
    )
    trusted_before = source_to_trusted_target_mapping.get_target_line_from_source_line(
        before_source_line
    )
    trusted_after = source_to_trusted_target_mapping.get_target_line_from_source_line(
        after_source_line
    )
    if (
        working_before is None
        or working_after is None
        or trusted_before is None
        or trusted_after is None
        or working_after <= working_before
        or trusted_after <= trusted_before
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_before
        )
        != working_before
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_after
        )
        != working_after
    ):
        return None

    trusted_parent_start = trusted_before
    trusted_parent_end = trusted_after - 1
    working_parent_start = working_before
    working_parent_end = working_after - 1
    if (
        trusted_parent_end - trusted_parent_start
        != working_parent_end - working_parent_start
        or trusted_parent_end - trusted_parent_start != origin.old_line_count
    ):
        return None
    for offset in range(trusted_parent_end - trusted_parent_start):
        trusted_line = trusted_parent_start + offset + 1
        working_line = working_parent_start + offset + 1
        if (
            trusted_target_to_working_mapping.get_target_line_from_source_line(
                trusted_line
            )
            != working_line
            or trusted_target_lines[trusted_line - 1] != working_lines[working_line - 1]
        ):
            return None

    return _TrustedPartialReplacementContext(
        working_parent_start,
        working_parent_end,
    )


def _plan_partial_replacement_from_trusted_target(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    claim: AbsenceClaim,
    origin: ReplacementUnitOrigin | None,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    parent_context: _TrustedPartialReplacementContext | None,
    source_to_working_mapping: LineMapping | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
) -> tuple[int, int] | None:
    """Plan one selected child around a trusted, already-updated sibling.

    A historical replacement can be split across batches.  A sibling batch
    may already have changed and reordered the unselected side, so replacing
    the selected old bytes in place would produce the wrong order.  Permit a
    separate removal and insertion only when the index and worktree agree on
    the complete parent span, the old bytes remain unique inside that parent,
    and the immediate desired-source neighbors identify one exact insertion
    boundary.

    Return the selected old-side target bounds when the edit was planned.
    """
    if (
        origin is None
        or origin.baseline_reference is None
        or claim.baseline_reference is None
        or parent_context is None
        or source_to_working_mapping is None
        or source_to_trusted_target_mapping is None
        or trusted_target_to_working_mapping is None
        or len(claimed_ranges) != 1
    ):
        return None

    source_start, source_end = claimed_ranges[0]
    selected_line_count = source_end - source_start + 1
    if (
        source_start < origin.new_start
        or source_end > origin.new_end
        or source_start <= 1
        or source_end >= len(source_lines)
        or _replacement_counts_cover_origin(
            origin,
            selected_line_count,
            len(claim.content_lines),
        )
    ):
        return None

    parent_after_line = origin.baseline_reference.after_line
    claim_after_line = claim.baseline_reference.after_line
    if parent_after_line is None or claim_after_line is None:
        return None
    working_parent_start = parent_context.working_parent_start
    working_parent_end = parent_context.working_parent_end
    old_offset = claim_after_line - parent_after_line
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    removal_start = working_parent_start + old_offset
    removal_end = removal_start + len(forbidden_sequence)
    recorded_old_side_matches = (
        old_offset >= 0
        and removal_end <= working_parent_end
        and _line_slice_matches(
            working_lines,
            removal_start,
            forbidden_sequence,
        )
    )
    if not recorded_old_side_matches:
        if parent_context.parent_occurrences is None:
            parent_context.parent_occurrences = LinePayloadOccurrenceIndex(
                workspace,
                working_lines,
                normalize_payloads=False,
                target_indexes=range(
                    working_parent_start,
                    working_parent_end,
                ),
            )
        parent_occurrences = parent_context.parent_occurrences
        rarest_offset: int | None = None
        rarest_count: int | None = None
        for offset, content in enumerate(forbidden_sequence):
            occurrence_count = parent_occurrences.occurrence_count(content)
            if rarest_count is None or occurrence_count < rarest_count:
                rarest_offset = offset
                rarest_count = occurrence_count
        if rarest_offset is None or rarest_count != 1:
            return None
        rarest_target = next(
            parent_occurrences.matching_line_indexes(forbidden_sequence[rarest_offset])
        )
        removal_start = rarest_target - rarest_offset
        removal_end = removal_start + len(forbidden_sequence)
        if (
            removal_start < working_parent_start
            or removal_end > working_parent_end
            or not _line_slice_matches(
                working_lines,
                removal_start,
                forbidden_sequence,
            )
        ):
            return None

    previous_target = source_to_working_mapping.get_target_line_from_source_line(
        source_start - 1
    )
    next_target = source_to_working_mapping.get_target_line_from_source_line(
        source_end + 1
    )
    trusted_previous = (
        source_to_trusted_target_mapping.get_target_line_from_source_line(
            source_start - 1
        )
    )
    trusted_next = source_to_trusted_target_mapping.get_target_line_from_source_line(
        source_end + 1
    )
    if (
        previous_target is None
        or next_target is None
        or trusted_previous is None
        or trusted_next is None
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_previous
        )
        != previous_target
        or trusted_target_to_working_mapping.get_target_line_from_source_line(
            trusted_next
        )
        != next_target
    ):
        return None
    insertion_start = previous_target
    insertion_end = next_target - 1
    if (
        insertion_start < working_parent_start
        or insertion_end > working_parent_end
        or insertion_end < insertion_start
    ):
        return None
    if insertion_start == insertion_end:
        plan.add_removal(removal_start, removal_end)
        plan.add_source_ranges(
            insertion_start,
            insertion_start,
            (
                (source_range_start, source_range_end)
                for source_range_start, source_range_end in claimed_ranges
            ),
        )
    elif insertion_start == removal_start and insertion_end == removal_end:
        plan.add_source_ranges(
            removal_start,
            removal_end,
            (
                (source_range_start, source_range_end)
                for source_range_start, source_range_end in claimed_ranges
            ),
        )
    else:
        return None
    return removal_start, removal_end


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


def _replacement_edit_fits_mapped_source_neighbors(
    edit: _BaselineRemovalEdit,
    claim: AbsenceClaim,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_lines: Sequence[bytes] | None,
    target_line_count: int,
    mapping: LineMapping | None,
    mapped_source_lines: Sequence[tuple[int, ...]] | None,
) -> bool:
    """Return whether mapped source neighbors permit an in-place edit.

    A selected split child can move relative to an already-realized sibling.
    Historical coordinates still identify its old bytes in that case, but
    inserting the new side there would silently restore the historical order.
    Nearest mapped source neighbors provide one-sided bounds even when live
    target-only content leaves the exact insertion gap underdetermined. A
    mapped line between fragmented payload ranges makes one combined edit
    structurally impossible and is rejected.
    """
    target_start, target_end = edit
    if not 0 <= target_start <= target_end <= target_line_count:
        return False
    if mapping is None or mapped_source_lines is None:
        return True
    if _edit_consumes_adjacent_mapped_source_alternative(
        edit,
        claim,
        claimed_ranges,
        source_lines,
        mapping,
    ):
        return True

    source_start = claimed_ranges[0][0]
    source_end = claimed_ranges[-1][1]
    lower = 0
    upper = len(mapped_source_lines)
    while lower < upper:
        middle = (lower + upper) // 2
        if mapped_source_lines[middle][0] < source_start:
            lower = middle + 1
        else:
            upper = middle
    first_mapped_at_or_after = lower

    if first_mapped_at_or_after > 0:
        mapped_before = mapping.get_target_line_from_source_line(
            mapped_source_lines[first_mapped_at_or_after - 1][0]
        )
        if mapped_before is not None and target_start < mapped_before:
            return False
    if (
        first_mapped_at_or_after < len(mapped_source_lines)
        and mapped_source_lines[first_mapped_at_or_after][0] <= source_end
    ):
        return False

    if first_mapped_at_or_after < len(mapped_source_lines):
        mapped_after = mapping.get_target_line_from_source_line(
            mapped_source_lines[first_mapped_at_or_after][0]
        )
        if mapped_after is not None and target_end > mapped_after - 1:
            return False
    return True


def _edit_consumes_adjacent_mapped_source_alternative(
    edit: _BaselineRemovalEdit,
    claim: AbsenceClaim,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_lines: Sequence[bytes] | None,
    mapping: LineMapping,
) -> bool:
    """Prove an edit consumes the explicit old side stored after its new side.

    A transformed replacement stores both alternatives next to each other in
    the batch source while marking the retained live side as a
    ``source_alternative`` absence claim.  That old side can consequently be a
    mapped source neighbor of the owned new side.  It is safe to consume that
    neighbor only when the exact recorded payload occupies the immediately
    following source span and every source line maps consecutively onto the
    proposed removal span.
    """
    if not claim.source_alternative or source_lines is None or len(claimed_ranges) != 1:
        return False

    target_start, target_end = edit
    alternative_lines = normalize_line_sequence_endings(claim.content_lines)
    alternative_line_count = len(alternative_lines)
    if (
        alternative_line_count == 0
        or target_end - target_start != alternative_line_count
    ):
        return False

    alternative_source_start = claimed_ranges[0][1] + 1
    alternative_source_end = alternative_source_start + alternative_line_count - 1
    if alternative_source_end > len(source_lines):
        return False

    for offset, expected_line in enumerate(alternative_lines):
        source_line = alternative_source_start + offset
        target_line = target_start + offset + 1
        if (
            source_lines[source_line - 1] != expected_line
            or mapping.get_target_line_from_source_line(source_line) != target_line
            or mapping.get_source_line_from_target_line(target_line) != source_line
        ):
            return False
    return True


def trusted_target_replacement_source_ranges(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes],
    source_to_working_mapping: LineMapping,
    source_to_trusted_target_mapping: LineMapping,
    trusted_target_to_working_mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
) -> LineRanges:
    """Return complete replacement ranges whose exact preimage is trusted.

    The returned ranges are compact provenance: before apply, the selected
    replacement's complete target span was byte-for-byte unchanged from the
    index.  A later discard may therefore restore that index span instead of
    the historical old side recorded by the batch.
    """
    trusted_ranges = LineRangeBuilder()
    deletion_claims = ownership.deletions
    replacement_units = ownership.replacement_units
    selected_presence = ownership.presence_line_set()
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        unit_index = 0
        while unit_index < len(replacement_units):
            group_end = unit_index + 1
            origin = replacement_units[unit_index].origin
            if origin is not None:
                while (
                    group_end < len(replacement_units)
                    and replacement_units[group_end].origin == origin
                ):
                    group_end += 1

            trusted_group_bounds = _trusted_mapped_replacement_group_bounds(
                workspace,
                replacement_units,
                unit_index,
                group_end,
                deletion_claims,
                selected_presence,
                source_lines,
                working_lines,
                source_to_working_mapping,
                trusted_target_lines,
                source_to_trusted_target_mapping,
                trusted_target_to_working_mapping,
            )
            if trusted_group_bounds is not None:
                if _replacement_group_old_side_matches_target(
                    replacement_units,
                    unit_index,
                    group_end,
                    deletion_claims,
                    working_lines,
                    trusted_group_bounds,
                ):
                    unit_index = group_end
                    continue
                for group_unit_index in range(unit_index, group_end):
                    group_ranges = _collect_replacement_source_ranges(
                        workspace,
                        replacement_units[group_unit_index].presence_lines,
                    )
                    if group_ranges is None:
                        break
                    try:
                        for source_start, source_end in group_ranges:
                            trusted_ranges.add_range(source_start, source_end)
                    finally:
                        workspace.close_resource(group_ranges)
                else:
                    unit_index = group_end
                    continue

            for child_index in range(unit_index, group_end):
                unit = replacement_units[child_index]
                if len(unit.deletion_indices) != 1:
                    continue
                deletion_index = unit.deletion_indices[0]
                if (
                    type(deletion_index) is not int
                    or deletion_index < 0
                    or deletion_index >= len(deletion_claims)
                ):
                    continue
                claimed_ranges = _collect_replacement_source_ranges(
                    workspace,
                    unit.presence_lines,
                )
                if claimed_ranges is None:
                    continue
                try:
                    trusted_edit = _replacement_edit_from_trusted_target(
                        deletion_claims[deletion_index],
                        unit.origin,
                        claimed_ranges,
                        len(source_lines),
                        source_lines,
                        working_lines,
                        trusted_target_lines,
                        source_to_working_mapping,
                        source_to_trusted_target_mapping,
                        trusted_target_to_working_mapping,
                        allow_mapped_source_predecessor=(len(replacement_units) == 1),
                    )
                    if trusted_edit is not None and not _line_slice_matches(
                        working_lines,
                        trusted_edit[0],
                        normalize_line_sequence_endings(
                            deletion_claims[deletion_index].content_lines
                        ),
                    ):
                        for source_start, source_end in claimed_ranges:
                            trusted_ranges.add_range(source_start, source_end)
                finally:
                    workspace.close_resource(claimed_ranges)
            unit_index = group_end
    return trusted_ranges.finish()


def _replacement_group_old_side_matches_target(
    replacement_units: Sequence[ReplacementUnit],
    group_start: int,
    group_end: int,
    deletion_claims: Sequence[AbsenceClaim],
    target_lines: Sequence[bytes],
    target_bounds: tuple[int, int],
) -> bool:
    """Return whether a group target span is still its historical old side."""
    target_position, target_end = target_bounds
    for unit_index in range(group_start, group_end):
        deletion_index = replacement_units[unit_index].deletion_indices[0]
        old_side = normalize_line_sequence_endings(
            deletion_claims[deletion_index].content_lines
        )
        if not _line_slice_matches(
            target_lines,
            target_position,
            old_side,
        ):
            return False
        target_position += len(old_side)
    return target_position == target_end


def _plan_complete_unrealized_origin_group(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    replacement_units: Sequence[ReplacementUnit],
    group_start: int,
    group_end: int,
    deletion_claims: Sequence[AbsenceClaim],
    selected_presence: LineRanges,
    deletion_edit_bounds: MappedRecordVector,
    replacement_source_ranges: MappedRecordVector,
    source_to_working_mapping: LineMapping | None,
    mapped_source_lines: Sequence[tuple[int, ...]] | None,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
    live_occurrence_index: LinePayloadOccurrenceIndex | None,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Plan consecutive split children as their complete exact parent."""
    if source_to_working_mapping is None or mapped_source_lines is None:
        return False
    origin = replacement_units[group_start].origin
    target_bounds = (
        None
        if origin is None or origin.baseline_reference is None
        else _complete_group_bounds(
            workspace,
            replacement_units,
            group_start,
            group_end,
            deletion_claims,
            selected_presence,
            source_lines,
            working_lines,
            source_to_working_mapping,
            origin,
            spool_dir=spool_dir,
            mapped_source_lines=mapped_source_lines,
        )
    )
    if target_bounds is None:
        target_bounds = _trusted_mapped_replacement_group_bounds(
            workspace,
            replacement_units,
            group_start,
            group_end,
            deletion_claims,
            selected_presence,
            source_lines,
            working_lines,
            source_to_working_mapping,
            trusted_target_lines,
            source_to_trusted_target_mapping,
            trusted_target_to_working_mapping,
            live_occurrence_index,
        )
    if target_bounds is None:
        return False

    group_range_capacity = sum(
        _replacement_source_range_capacity(replacement_units[unit_index].presence_lines)
        for unit_index in range(group_start, group_end)
    )
    group_source_ranges = workspace.record_vector(
        group_range_capacity,
        "QQ",
    )
    try:
        first_unit = replacement_units[group_start]
        first_deletion_index = first_unit.deletion_indices[0]
        first_reference = deletion_claims[first_deletion_index].baseline_reference
        if first_reference is None or first_reference.after_line is None:
            return False
        parent_after_line = first_reference.after_line
        parent_start, parent_end = target_bounds
        for unit_index in range(group_start, group_end):
            unit = replacement_units[unit_index]
            claimed_ranges = _collect_replacement_source_ranges(
                workspace,
                unit.presence_lines,
            )
            if claimed_ranges is None:
                return False
            try:
                for source_start, source_end in claimed_ranges:
                    group_source_ranges.append((source_start, source_end))
                    replacement_source_ranges.append(
                        (
                            source_start,
                            source_end,
                        )
                    )
            finally:
                workspace.close_resource(claimed_ranges)

            deletion_index = unit.deletion_indices[0]
            if deletion_edit_bounds[deletion_index][0]:
                return False
            claim = deletion_claims[deletion_index]
            assert claim.baseline_reference is not None
            old_offset = (claim.baseline_reference.after_line or 0) - parent_after_line
            child_start = parent_start + old_offset
            deletion_edit_bounds[deletion_index] = (
                1,
                child_start,
                child_start + len(claim.content_lines),
                1,
            )

        plan.add_source_ranges(
            parent_start,
            parent_end,
            (
                (source_start, source_end)
                for source_start, source_end in group_source_ranges
            ),
        )
        return True
    finally:
        workspace.close_resource(group_source_ranges)


def _trusted_mapped_replacement_group_bounds(
    workspace: MatcherWorkspace,
    replacement_units: Sequence[ReplacementUnit],
    group_start: int,
    group_end: int,
    deletion_claims: Sequence[AbsenceClaim],
    selected_presence: LineRanges,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    source_to_working_mapping: LineMapping,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
    live_occurrence_index: LinePayloadOccurrenceIndex | None = None,
) -> tuple[int, int] | None:
    """Return a complete old-side gap proven by source and index mappings.

    Historical replacement text can be stale after an adjacent committed
    transformation.  The transformed span is still safe to replace when all
    desired lines are absent, mapped source neighbors bracket exactly the
    deletion-sized gap, and every gap line maps byte-for-byte from the trusted
    index to the worktree.
    """
    if (
        trusted_target_lines is None
        or source_to_trusted_target_mapping is None
        or trusted_target_to_working_mapping is None
    ):
        return None

    first_source_line: int | None = None
    next_source_line: int | None = None
    deletion_line_count = 0
    next_baseline_after_line: int | None = None
    for unit_index in range(group_start, group_end):
        unit = replacement_units[unit_index]
        if len(unit.deletion_indices) != 1:
            return None
        deletion_index = unit.deletion_indices[0]
        if (
            type(deletion_index) is not int
            or deletion_index < 0
            or deletion_index >= len(deletion_claims)
        ):
            return None
        claim = deletion_claims[deletion_index]
        claim_reference = claim.baseline_reference
        if (
            not claim.content_lines
            or claim_reference is None
            or not claim_reference.has_after_line
            or claim_reference.after_line is None
            or (
                next_baseline_after_line is not None
                and claim_reference.after_line != next_baseline_after_line
            )
        ):
            return None

        claimed_ranges = _collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        if claimed_ranges is None:
            return None
        try:
            if len(claimed_ranges) != 1:
                return None
            source_start, source_end = claimed_ranges[0]
            if source_end > len(source_lines) or (
                next_source_line is not None and source_start != next_source_line
            ):
                return None
            for source_line in range(source_start, source_end + 1):
                if (
                    source_line not in selected_presence
                    or source_to_working_mapping.get_target_line_from_source_line(
                        source_line
                    )
                    is not None
                    or source_to_trusted_target_mapping.get_target_line_from_source_line(
                        source_line
                    )
                    is not None
                ):
                    return None
            if first_source_line is None:
                first_source_line = source_start
            next_source_line = source_end + 1
        finally:
            workspace.close_resource(claimed_ranges)

        deletion_line_count += len(claim.content_lines)
        next_baseline_after_line = claim_reference.after_line + len(claim.content_lines)

    if (
        first_source_line is None
        or next_source_line is None
        or first_source_line <= 1
        or next_source_line > len(source_lines)
        or deletion_line_count == 0
    ):
        return None

    before_source_line = first_source_line - 1
    after_source_line = next_source_line
    working_before = source_to_working_mapping.get_target_line_from_source_line(
        before_source_line
    )
    working_after = source_to_working_mapping.get_target_line_from_source_line(
        after_source_line
    )
    trusted_before = source_to_trusted_target_mapping.get_target_line_from_source_line(
        before_source_line
    )
    trusted_after = source_to_trusted_target_mapping.get_target_line_from_source_line(
        after_source_line
    )
    if (
        working_before is not None
        and working_after is not None
        and trusted_before is not None
        and trusted_after is not None
    ):
        working_start = working_before
        working_end = working_after - 1
        trusted_start = trusted_before
        trusted_end = trusted_after - 1
        if (
            working_end - working_start == deletion_line_count
            and trusted_end - trusted_start == deletion_line_count
            and (
                trusted_target_to_working_mapping.get_target_line_from_source_line(
                    trusted_before
                )
            )
            == working_before
            and (
                trusted_target_to_working_mapping.get_target_line_from_source_line(
                    trusted_after
                )
            )
            == working_after
        ):
            for offset in range(deletion_line_count):
                trusted_line = trusted_start + offset + 1
                working_line = working_start + offset + 1
                if (
                    trusted_target_to_working_mapping.get_target_line_from_source_line(
                        trusted_line
                    )
                    != working_line
                    or trusted_target_lines[trusted_line - 1]
                    != working_lines[working_line - 1]
                ):
                    break
            else:
                return working_start, working_end

    if (
        group_end != group_start + 1
        or replacement_units[group_start].origin is not None
        or live_occurrence_index is None
    ):
        return None
    legacy_bounds = _unique_live_removal_context_bounds(
        claim,
        working_lines,
        deletion_line_count,
        live_occurrence_index,
    )
    if legacy_bounds is None or not _trusted_target_span_matches_working(
        working_lines,
        trusted_target_lines,
        trusted_target_to_working_mapping,
        legacy_bounds[0],
        legacy_bounds[1],
    ):
        return None
    return legacy_bounds


def plan_replacement_unit_edits(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    source_lines: Sequence[bytes] | int,
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
    selected_presence: LineRanges | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    trust_baseline_coordinates: bool = False,
    allow_mixed_mapped_replacement_islands: bool = False,
    mapped_source_lines: Sequence[tuple[int, ...]] | None = None,
) -> bool:
    """Plan coupled replacement units and record their claimed source ranges."""
    if isinstance(source_lines, int):
        source_line_count = source_lines
        source_sequence = None
    else:
        source_line_count = len(source_lines)
        source_sequence = source_lines
    if selected_presence is None:
        selected_presence = LineRanges.from_specs(
            source_line
            for unit in replacement_units
            for source_line in unit.presence_lines
        )
    if mapped_source_lines is None and source_to_working_mapping is not None:
        mapped_source_lines = _build_mapped_source_line_index(
            workspace,
            source_to_working_mapping,
        )
    skip_until = 0
    partial_context_until = 0
    partial_context: _TrustedPartialReplacementContext | None = None
    live_occurrence_index: LinePayloadOccurrenceIndex | None = None
    previous_mixed_target_end = 0
    for unit_index, unit in enumerate(replacement_units):
        if unit_index < skip_until:
            continue
        if unit_index >= partial_context_until:
            if partial_context is not None:
                partial_context.close()
            group_end = unit_index + 1
            origin = unit.origin
            if origin is not None:
                while (
                    group_end < len(replacement_units)
                    and replacement_units[group_end].origin == origin
                ):
                    group_end += 1
            if (
                origin is None
                and live_occurrence_index is None
                and source_to_working_mapping is not None
                and trusted_target_lines is not None
                and source_to_trusted_target_mapping is not None
                and trusted_target_to_working_mapping is not None
            ):
                live_occurrence_index = LinePayloadOccurrenceIndex(
                    workspace,
                    working_lines,
                )
            if (
                source_sequence is not None
                and (group_end > unit_index + 1 or origin is None)
                and _plan_complete_unrealized_origin_group(
                    workspace,
                    plan,
                    source_sequence,
                    working_lines,
                    replacement_units,
                    unit_index,
                    group_end,
                    deletion_claims,
                    selected_presence,
                    deletion_edit_bounds,
                    replacement_source_ranges,
                    source_to_working_mapping,
                    mapped_source_lines,
                    trusted_target_lines,
                    source_to_trusted_target_mapping,
                    trusted_target_to_working_mapping,
                    live_occurrence_index,
                    spool_dir=spool_dir,
                )
            ):
                skip_until = group_end
                continue
            partial_context_until = group_end
            partial_context = (
                None
                if (
                    source_sequence is None
                    or (len(replacement_units) == 1 and resolution is None)
                )
                else _trusted_partial_replacement_context(
                    workspace,
                    origin,
                    source_sequence,
                    working_lines,
                    trusted_target_lines,
                    source_to_working_mapping,
                    source_to_trusted_target_mapping,
                    trusted_target_to_working_mapping,
                )
            )

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
                trusted_mixed_edit = _replacement_edit_from_trusted_target(
                    claim,
                    unit.origin,
                    claimed_ranges,
                    source_line_count,
                    source_sequence,
                    working_lines,
                    trusted_target_lines,
                    source_to_working_mapping,
                    source_to_trusted_target_mapping,
                    trusted_target_to_working_mapping,
                    allow_mapped_source_predecessor=(len(replacement_units) == 1),
                )
                if trusted_mixed_edit is not None:
                    target_start, target_end = trusted_mixed_edit
                    plan.add_source_ranges(
                        target_start,
                        target_end,
                        (
                            (source_start, source_end)
                            for source_start, source_end in claimed_ranges
                        ),
                    )
                    for source_start, source_end in claimed_ranges:
                        replacement_source_ranges.append((source_start, source_end))
                    deletion_edit_bounds[deletion_index] = (
                        1,
                        target_start,
                        target_end,
                        1,
                    )
                    continue
                mixed_bounds = (
                    None
                    if (
                        not allow_mixed_mapped_replacement_islands
                        or source_to_working_mapping is None
                    )
                    else _mixed_mapped_replacement_bounds(
                        claimed_ranges,
                        source_to_working_mapping,
                        minimum_target_start=previous_mixed_target_end,
                    )
                )
                if mixed_bounds is None:
                    return False
                target_start, target_end = mixed_bounds
                previous_mixed_target_end = target_end
                plan.add_source_ranges(
                    target_start,
                    target_end,
                    (
                        (source_start, source_end)
                        for source_start, source_end in claimed_ranges
                    ),
                )
                for source_start, source_end in claimed_ranges:
                    replacement_source_ranges.append((source_start, source_end))
                deletion_edit_bounds[deletion_index] = (
                    1,
                    target_start,
                    target_end,
                    1,
                )
                continue
            if replacement_is_mapped:
                assert source_to_working_mapping is not None
                old_side = _classify_replacement_old_side(
                    claim,
                    working_lines,
                    source_to_working_mapping,
                    claimed_ranges,
                    spool_dir=spool_dir,
                    mapped_source_lines=mapped_source_lines,
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

            partial_reviewed_bounds = (
                None
                if source_sequence is None
                else _plan_partial_replacement_from_origin_resolution(
                    plan,
                    claim,
                    unit_index,
                    unit,
                    claimed_ranges,
                    source_line_count,
                    source_to_working_mapping,
                    working_lines,
                    resolution,
                    max_results=max_resolution_choices,
                )
            )
            if partial_reviewed_bounds is not None:
                removal_start, removal_end = partial_reviewed_bounds
                for source_start, source_end in claimed_ranges:
                    replacement_source_ranges.append(
                        (
                            source_start,
                            source_end,
                        )
                    )
                deletion_edit_bounds[deletion_index] = (
                    1,
                    removal_start,
                    removal_end,
                    1,
                )
                continue

            partial_trusted_bounds = (
                None
                if (
                    source_sequence is None
                    or (len(replacement_units) == 1 and resolution is None)
                )
                else _plan_partial_replacement_from_trusted_target(
                    workspace,
                    plan,
                    claim,
                    unit.origin,
                    claimed_ranges,
                    source_sequence,
                    working_lines,
                    partial_context,
                    source_to_working_mapping,
                    source_to_trusted_target_mapping,
                    trusted_target_to_working_mapping,
                )
            )
            if partial_trusted_bounds is not None:
                removal_start, removal_end = partial_trusted_bounds
                for source_start, source_end in claimed_ranges:
                    replacement_source_ranges.append(
                        (
                            source_start,
                            source_end,
                        )
                    )
                deletion_edit_bounds[deletion_index] = (
                    1,
                    removal_start,
                    removal_end,
                    1,
                )
                continue

            replacement_edit = _replacement_baseline_edit(
                claim,
                unit_index,
                unit,
                claimed_ranges,
                source_line_count,
                source_sequence,
                working_lines,
                trusted_target_lines,
                source_to_working_mapping,
                source_to_trusted_target_mapping,
                trusted_target_to_working_mapping,
                resolution,
                max_resolution_choices=max_resolution_choices,
                allow_mapped_source_predecessor=(len(replacement_units) == 1),
            )
            if (
                replacement_edit is None
                and unit.origin is None
                and not trust_baseline_coordinates
            ):
                if live_occurrence_index is None:
                    live_occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        working_lines,
                    )
                shifted_edit = _unique_live_removal_edit(
                    claim,
                    working_lines,
                    live_occurrence_index,
                )
                if shifted_edit is not None:
                    if (
                        not trust_baseline_coordinates
                        and not _replacement_edit_fits_mapped_source_neighbors(
                            shifted_edit,
                            claim,
                            claimed_ranges,
                            source_sequence,
                            len(working_lines),
                            source_to_working_mapping,
                            mapped_source_lines,
                        )
                    ):
                        return False
                    replacement_edit = shifted_edit, True
            if replacement_edit is None:
                return False

            removal_edit, coordinate_was_reviewed = replacement_edit
            start, end = removal_edit
            if (
                not coordinate_was_reviewed
                and not trust_baseline_coordinates
                and not _replacement_edit_fits_mapped_source_neighbors(
                    removal_edit,
                    claim,
                    claimed_ranges,
                    source_sequence,
                    len(working_lines),
                    source_to_working_mapping,
                    mapped_source_lines,
                )
            ):
                return False
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

    if partial_context is not None:
        partial_context.close()
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
