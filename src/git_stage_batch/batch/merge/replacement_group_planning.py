"""Verify and plan replacement groups against a trusted target."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import (
    trusted_target_span_matches_working as _trusted_target_span_matches_working,
    unique_live_removal_context_bounds as _unique_live_removal_context_bounds,
)
from .baseline_edit_plan import BaselineEditPlan
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    replacement_source_range_capacity as _replacement_source_range_capacity,
)
from .validation import (
    complete_unrealized_replacement_group_target_bounds as _complete_group_bounds,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..line_matching.sequence_equality import line_slice_equals as _line_slice_matches
from ..ownership.replacement_units import (
    replacement_counts_cover_origin as _replacement_counts_cover_origin,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.replacement_units import ReplacementUnit, ReplacementUnitOrigin


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
