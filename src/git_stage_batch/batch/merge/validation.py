"""Structural safety validation for batch merge placement."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import AbstractSet, TYPE_CHECKING, cast, overload

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector
from ...core.text_lines import normalize_line_sequence_endings
from ...exceptions import MergeError as _MergeError
from ...i18n import _, ngettext
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..ownership.replacement_units import replacement_counts_cover_origin
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    selected_replacement_source_ranges as _selected_replacement_source_ranges,
)
from ..line_matching.sequence_equality import (
    line_slice_equals as _line_slice_matches,
)
from .presence_context import (
    PresenceRunPlacement,
    contextual_presence_placements as _contextual_presence_placements,
    _iter_missing_presence_clusters,
)
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.replacement_units import (
        ReplacementUnit,
        ReplacementUnitOrigin,
    )


@dataclass(frozen=True, slots=True)
class _ReplacementUnitMappingState:
    """Selected-line realization state for one replacement unit."""

    range_count: int
    selected_line_count: int
    has_mapped_line: bool
    has_missing_line: bool
    has_out_of_bounds_line: bool


class ReplacementOldSideState(Enum):
    """Structural state of a replacement unit's coupled old side."""

    FULL = "full"
    ABSENT = "absent"
    PARTIAL = "partial"


def build_mapped_source_line_index(
    workspace: MatcherWorkspace,
    mapping: LineMapping,
) -> MappedRecordVector:
    """Index mapped source lines once for repeated boundary classification."""
    mapped_lines = workspace.record_vector(
        len(mapping.source_to_target),
        "Q",
    )
    for source_index in range(len(mapping.source_to_target)):
        if mapping.source_to_target[source_index] != 0:
            mapped_lines.append((source_index + 1,))
    return mapped_lines


@dataclass(frozen=True, slots=True)
class ReplacementOldSideRealization:
    """Old-side state and its exact removable target position, if any."""

    state: ReplacementOldSideState
    target_position: int | None = None


_CLAIMED_TARGET_LINE = object()


class _UnclaimedTargetGap(Sequence[Hashable]):
    """Target-gap view that masks content owned by selected presence lines."""

    def __init__(
        self,
        target_lines: Sequence[bytes],
        start: int,
        end: int,
        mapping: LineMapping,
        claimed_ranges: Sequence[tuple[int, ...]],
        *,
        masked_start: int | None = None,
        masked_end: int | None = None,
    ) -> None:
        self._target_lines = target_lines
        self._start = start
        self._end = end
        self._mapping = mapping
        self._claimed_ranges = claimed_ranges
        self._masked_start = masked_start
        self._masked_end = masked_end

    def __len__(self) -> int:
        return self._end - self._start

    @overload
    def __getitem__(self, index: int) -> Hashable: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Hashable]: ...

    def __getitem__(self, index: int | slice) -> Hashable | Sequence[Hashable]:
        if isinstance(index, slice):
            return tuple(
                self[child_index]
                for child_index in range(*index.indices(len(self)))
            )
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)

        target_index = self._start + index
        if (
            self._masked_start is not None
            and self._masked_end is not None
            and self._masked_start <= target_index < self._masked_end
        ):
            return _CLAIMED_TARGET_LINE
        source_line = self._mapping.get_source_line_from_target_line(
            target_index + 1
        )
        if source_line is not None and _line_is_claimed(
            self._claimed_ranges,
            source_line,
        ):
            return _CLAIMED_TARGET_LINE
        return self._target_lines[target_index]


def _replacement_unit_mapping_state(
    workspace: MatcherWorkspace,
    unit: ReplacementUnit,
    selected_presence: LineRanges,
    source_line_count: int,
    mapping: LineMapping,
) -> _ReplacementUnitMappingState | None:
    """Return selected mapping state, or None for malformed unit ranges."""
    claimed_ranges = _collect_replacement_source_ranges(
        workspace,
        unit.presence_lines,
    )
    if claimed_ranges is None:
        return None

    try:
        selected_line_count = 0
        has_mapped_line = False
        has_missing_line = False
        has_out_of_bounds_line = False
        for claimed_start, claimed_end in _selected_replacement_source_ranges(
            claimed_ranges,
            selected_presence,
        ):
            selected_line_count += claimed_end - claimed_start + 1
            for claimed_line in range(claimed_start, claimed_end + 1):
                if claimed_line > source_line_count:
                    has_out_of_bounds_line = True
                    has_missing_line = True
                elif mapping.get_target_line_from_source_line(claimed_line) is None:
                    has_missing_line = True
                else:
                    has_mapped_line = True
        return _ReplacementUnitMappingState(
            range_count=len(claimed_ranges),
            selected_line_count=selected_line_count,
            has_mapped_line=has_mapped_line,
            has_missing_line=has_missing_line,
            has_out_of_bounds_line=has_out_of_bounds_line,
        )
    finally:
        workspace.close_resource(claimed_ranges)


def has_missing_origin_replacement_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
) -> bool:
    """Return whether parent-tracked replacement lines would need placement."""
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        mapped_source_lines = build_mapped_source_line_index(
            workspace,
            mapping,
        )
        replacement_units = getattr(ownership, "replacement_units", [])
        unit_index = 0
        while unit_index < len(replacement_units):
            unit = replacement_units[unit_index]
            origin = getattr(unit, "origin", None)
            if origin is None:
                unit_index += 1
                continue
            group_end = unit_index + 1
            while (
                group_end < len(replacement_units)
                and replacement_units[group_end].origin == origin
            ):
                group_end += 1
            if (
                group_end > unit_index + 1
                and complete_unrealized_replacement_group_target_bounds(
                    workspace,
                    replacement_units,
                    unit_index,
                    group_end,
                    ownership.deletions,
                    selected_presence,
                    source_lines,
                    target_lines,
                    mapping,
                    origin,
                    spool_dir=spool_dir,
                    mapped_source_lines=mapped_source_lines,
                ) is not None
            ):
                unit_index = group_end
                continue
            for child_index in range(unit_index, group_end):
                child = replacement_units[child_index]
                state = _replacement_unit_mapping_state(
                    workspace,
                    child,
                    selected_presence,
                    len(source_lines),
                    mapping,
                )
                if state is None or state.has_out_of_bounds_line:
                    return True
                if state.selected_line_count == 0:
                    continue
                unit_covers_origin = (
                    state.range_count == 1
                    and _selected_unit_covers_replacement_origin(
                        child.deletion_indices,
                        ownership.deletions,
                        origin,
                        state.selected_line_count,
                    )
                )
                if not state.has_missing_line:
                    continue
                old_side = _replacement_old_side_realization(
                    child.deletion_indices,
                    ownership.deletions,
                    target_lines,
                    mapping,
                    selected_presence,
                    spool_dir=spool_dir,
                    mapped_source_lines=mapped_source_lines,
                )
                if (
                    old_side is None
                    or old_side.state is ReplacementOldSideState.PARTIAL
                ):
                    return True
                if not unit_covers_origin or state.has_mapped_line:
                    return True
                if old_side.state is not ReplacementOldSideState.FULL:
                    return True
            unit_index = group_end
    return False


def has_fragmented_replacement_crossing_mapped_source_lines(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
) -> bool:
    """Return whether one missing fragmented payload crosses live source.

    Structural presence placement can otherwise collect both fragments at one
    gap and silently move them across an interleaved source line. Coordinate
    replay may handle such metadata when it can prove separate placement; the
    generic structural fallback must fail closed.
    """
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        mapped_source_lines = build_mapped_source_line_index(
            workspace,
            mapping,
        )
        for unit in getattr(ownership, "replacement_units", []):
            claimed_ranges = _collect_replacement_source_ranges(
                workspace,
                unit.presence_lines,
            )
            if claimed_ranges is None:
                return True
            try:
                selected_range_count = 0
                first_selected_line = 0
                last_selected_line = 0
                has_missing_line = False
                for source_start, source_end in (
                    _selected_replacement_source_ranges(
                        claimed_ranges,
                        selected_presence,
                    )
                ):
                    if selected_range_count == 0:
                        first_selected_line = source_start
                    selected_range_count += 1
                    last_selected_line = source_end
                    if source_end > len(source_lines):
                        has_missing_line = True
                        continue
                    if any(
                        mapping.get_target_line_from_source_line(source_line)
                        is None
                        for source_line in range(source_start, source_end + 1)
                    ):
                        has_missing_line = True

                if selected_range_count < 2 or not has_missing_line:
                    continue
                first_mapped_record = (
                    _first_mapped_source_record_at_or_after(
                        mapped_source_lines,
                        first_selected_line,
                    )
                )
                if (
                    first_mapped_record < len(mapped_source_lines)
                    and mapped_source_lines[first_mapped_record][0]
                    <= last_selected_line
                ):
                    return True
            finally:
                workspace.close_resource(claimed_ranges)
    return False


def complete_unrealized_replacement_group_target_bounds(
    workspace: MatcherWorkspace,
    replacement_units: Sequence[ReplacementUnit],
    group_start: int,
    group_end: int,
    deletions: Sequence[AbsenceClaim],
    selected_presence: LineRanges,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    origin: ReplacementUnitOrigin,
    *,
    spool_dir: str | Path | None,
    mapped_source_lines: Sequence[tuple[int, ...]],
) -> tuple[int, int] | None:
    """Return exact target bounds for one complete unrealized split parent."""
    reference = origin.baseline_reference
    if reference is None or not reference.has_after_line:
        return None

    parent_after_line = reference.after_line or 0
    next_old_offset = 0
    next_source_line: int | None = None
    selected_new_line_count = 0
    parent_start: int | None = None
    for unit_index in range(group_start, group_end):
        unit = replacement_units[unit_index]
        if len(unit.deletion_indices) != 1:
            return None
        deletion_index = unit.deletion_indices[0]
        if (
            type(deletion_index) is not int
            or deletion_index < 0
            or deletion_index >= len(deletions)
        ):
            return None
        claim = deletions[deletion_index]
        claim_reference = claim.baseline_reference
        if (
            claim_reference is None
            or not claim_reference.has_after_line
            or claim_reference.after_line is None
        ):
            return None
        old_offset = claim_reference.after_line - parent_after_line
        if old_offset != next_old_offset or not claim.content_lines:
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
            if (
                source_end > len(source_lines)
                or (
                    next_source_line is not None
                    and source_start != next_source_line
                )
            ):
                return None
            for source_line in range(source_start, source_end + 1):
                if (
                    source_line not in selected_presence
                    or mapping.get_target_line_from_source_line(source_line)
                    is not None
                ):
                    return None
            if parent_start is None:
                first_old_side = classify_replacement_old_side(
                    claim,
                    target_lines,
                    mapping,
                    claimed_ranges,
                    spool_dir=spool_dir,
                    mapped_source_lines=mapped_source_lines,
                )
                if (
                    first_old_side is None
                    or first_old_side.state is not ReplacementOldSideState.FULL
                    or first_old_side.target_position is None
                ):
                    return None
                parent_start = first_old_side.target_position
            selected_new_line_count += source_end - source_start + 1
            next_source_line = source_end + 1
        finally:
            workspace.close_resource(claimed_ranges)

        assert parent_start is not None
        old_start = parent_start + old_offset
        if not _line_slice_matches(
            target_lines,
            old_start,
            normalize_line_sequence_endings(claim.content_lines),
        ):
            return None
        next_old_offset += len(claim.content_lines)

    if (
        next_old_offset != origin.old_line_count
        or selected_new_line_count != origin.new_end - origin.new_start + 1
        or parent_start is None
    ):
        return None
    return parent_start, parent_start + next_old_offset


def has_mapped_origin_replacement_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    unit_indices: AbstractSet[int] | None = None,
    spool_dir: str | Path | None = None,
) -> bool:
    """Return whether an origin-tracked unit has mapped new-side lines."""
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        for unit_index, unit in enumerate(
            getattr(ownership, "replacement_units", [])
        ):
            if unit_indices is not None and unit_index not in unit_indices:
                continue
            if getattr(unit, "origin", None) is None:
                continue
            state = _replacement_unit_mapping_state(
                workspace,
                unit,
                selected_presence,
                len(source_lines),
                mapping,
            )
            if state is not None and state.has_mapped_line:
                return True
    return False


def has_mixed_origin_replacement_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
) -> bool:
    """Return whether one origin unit mixes mapped and missing new lines."""
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        for unit in getattr(ownership, "replacement_units", []):
            if getattr(unit, "origin", None) is None:
                continue
            state = _replacement_unit_mapping_state(
                workspace,
                unit,
                selected_presence,
                len(source_lines),
                mapping,
            )
            if (
                state is not None
                and state.has_mapped_line
                and state.has_missing_line
            ):
                return True
    return False


def has_unsafe_mapped_origin_old_side_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
    max_classifications: int | None = None,
) -> bool:
    """Return whether a mapped origin unit has an unsafe coupled old side."""
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        mapped_source_lines = build_mapped_source_line_index(
            workspace,
            mapping,
        )
        classification_count = 0
        for unit in getattr(ownership, "replacement_units", []):
            if getattr(unit, "origin", None) is None:
                continue
            state = _replacement_unit_mapping_state(
                workspace,
                unit,
                selected_presence,
                len(source_lines),
                mapping,
            )
            if (
                state is None
                or state.selected_line_count == 0
                or not state.has_mapped_line
            ):
                continue
            if (
                max_classifications is not None
                and classification_count >= max_classifications
            ):
                # Each classification can inspect a broad structural gap.
                # Ordinary replay must stay bounded when legacy metadata
                # splits one dense mapped origin into many children.
                return True
            classification_count += 1
            old_side = _replacement_old_side_realization(
                unit.deletion_indices,
                ownership.deletions,
                target_lines,
                mapping,
                selected_presence,
                spool_dir=spool_dir,
                mapped_source_lines=mapped_source_lines,
            )
            if (
                old_side is None
                or old_side.state is ReplacementOldSideState.PARTIAL
            ):
                return True
    return False


def _selected_unit_covers_replacement_origin(
    deletion_indices: Sequence[int],
    deletions: Sequence[AbsenceClaim],
    origin: ReplacementUnitOrigin,
    selected_line_count: int,
) -> bool:
    """Return whether selected old/new content covers the complete origin."""
    if len(deletion_indices) != 1:
        return False
    deletion_index = deletion_indices[0]
    if (
        type(deletion_index) is not int
        or deletion_index < 0
        or deletion_index >= len(deletions)
    ):
        return False
    return replacement_counts_cover_origin(
        origin,
        selected_line_count,
        len(deletions[deletion_index].content_lines),
    )


def _replacement_old_side_realization(
    deletion_indices: Sequence[int],
    deletions: Sequence[AbsenceClaim],
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    claimed_lines: LineSelection | Sequence[tuple[int, ...]],
    *,
    spool_dir: str | Path | None,
    mapped_source_lines: Sequence[tuple[int, ...]] | None = None,
) -> ReplacementOldSideRealization | None:
    """Return one unit's old-side realization, or None when it is malformed."""
    if len(deletion_indices) != 1:
        return None
    deletion_index = deletion_indices[0]
    if (
        type(deletion_index) is not int
        or deletion_index < 0
        or deletion_index >= len(deletions)
    ):
        return None
    return classify_replacement_old_side(
        deletions[deletion_index],
        target_lines,
        mapping,
        claimed_lines,
        spool_dir=spool_dir,
        mapped_source_lines=mapped_source_lines,
    )


def classify_replacement_old_side(
    deletion: AbsenceClaim,
    target_lines: Sequence[bytes],
    mapping: LineMapping,
    claimed_lines: LineSelection | Sequence[tuple[int, ...]],
    *,
    spool_dir: str | Path | None = None,
    mapped_source_lines: Sequence[tuple[int, ...]] | None = None,
) -> ReplacementOldSideRealization | None:
    """Classify old-side content in the deletion's mapped structural gap.

    None means that the structural anchor or its following boundary cannot be
    resolved safely. Content outside that gap is deliberately ignored: an
    equal sequence elsewhere may be unrelated working-tree content.
    """
    claimed_ranges = _claimed_range_records(claimed_lines)
    deleted_sequence = normalize_line_sequence_endings(deletion.content_lines)
    if not deleted_sequence:
        return None
    if deletion.anchor_line is None:
        target_position = 0
        next_source_line = 1
    else:
        target_line = mapping.get_target_line_from_source_line(
            deletion.anchor_line
        )
        if target_line is None:
            return None
        target_position = target_line
        next_source_line = deletion.anchor_line + 1

    target_end_position = len(target_lines)
    candidate_source_lines: Iterable[int]
    if mapped_source_lines is None:
        candidate_source_lines = range(
            next_source_line,
            len(mapping.source_to_target) + 1,
        )
    else:
        first_record = _first_mapped_source_record_at_or_after(
            mapped_source_lines,
            next_source_line,
        )
        candidate_source_lines = (
            mapped_source_lines[index][0]
            for index in range(first_record, len(mapped_source_lines))
        )
    for source_line in candidate_source_lines:
        next_target_line = mapping.get_target_line_from_source_line(source_line)
        if next_target_line is None:
            continue
        if next_target_line <= target_position:
            return None
        if _line_is_claimed(claimed_ranges, source_line):
            continue
        target_end_position = next_target_line - 1
        break

    normalized_target = normalize_line_sequence_endings(target_lines)
    after_claimed_position = target_position
    while after_claimed_position < target_end_position:
        mapped_source_line = mapping.get_source_line_from_target_line(
            after_claimed_position + 1
        )
        if mapped_source_line is None or not _line_is_claimed(
            claimed_ranges,
            mapped_source_line,
        ):
            break
        after_claimed_position += 1

    target_gap = _UnclaimedTargetGap(
        normalized_target,
        target_position,
        target_end_position,
        mapping,
        claimed_ranges,
    )
    removal_positions = tuple(
        dict.fromkeys((target_position, after_claimed_position))
    )
    matching_positions = tuple(
        removal_position
        for removal_position in removal_positions
        if (
            removal_position + len(deleted_sequence) <= target_end_position
            and all(
                target_gap[
                    removal_position - target_position + offset
                ] == expected_line
                for offset, expected_line in enumerate(deleted_sequence)
            )
        )
    )
    if len(matching_positions) > 1:
        return ReplacementOldSideRealization(
            ReplacementOldSideState.PARTIAL
        )

    overlap_gap = target_gap
    if matching_positions:
        removal_position = matching_positions[0]
        overlap_gap = _UnclaimedTargetGap(
            normalized_target,
            target_position,
            target_end_position,
            mapping,
            claimed_ranges,
            masked_start=removal_position,
            masked_end=removal_position + len(deleted_sequence),
        )
    with match_lines(
        deleted_sequence,
        overlap_gap,
        spool_dir=spool_dir,
    ) as overlap:
        if (
            overlap.may_have_unmapped_equal_lines
            or next(overlap.mapped_line_pairs(), None) is not None
        ):
            return ReplacementOldSideRealization(
                ReplacementOldSideState.PARTIAL
            )
    if matching_positions:
        return ReplacementOldSideRealization(
            ReplacementOldSideState.FULL,
            matching_positions[0],
        )
    return ReplacementOldSideRealization(ReplacementOldSideState.ABSENT)


def _claimed_range_records(
    claimed_lines: LineSelection | Sequence[tuple[int, ...]],
) -> Sequence[tuple[int, ...]]:
    """Return normalized range records without copying mapped storage."""
    if isinstance(claimed_lines, LineRanges):
        return claimed_lines.ranges()
    ranges = getattr(claimed_lines, "ranges", None)
    if ranges is not None:
        return cast(Sequence[tuple[int, ...]], ranges())
    return cast(Sequence[tuple[int, ...]], claimed_lines)


def _first_mapped_source_record_at_or_after(
    mapped_source_lines: Sequence[tuple[int, ...]],
    source_line: int,
) -> int:
    """Return the first mapped-record index at or beyond a source line."""
    low = 0
    high = len(mapped_source_lines)
    while low < high:
        middle = (low + high) // 2
        if mapped_source_lines[middle][0] < source_line:
            low = middle + 1
        else:
            high = middle
    return low


def _line_is_claimed(
    claimed_ranges: Sequence[tuple[int, ...]],
    source_line: int,
) -> bool:
    """Return whether sorted inclusive range records contain a source line."""
    low = 0
    high = len(claimed_ranges)
    while low < high:
        middle = (low + high) // 2
        record = claimed_ranges[middle]
        if len(record) < 2 or record[0] > source_line:
            high = middle
        else:
            low = middle + 1
    if low == 0:
        return False
    record = claimed_ranges[low - 1]
    return len(record) >= 2 and record[0] <= source_line <= record[1]


def check_structural_validity(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    deletions: list[AbsenceClaim],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    *,
    require_distinctive_presence_context: bool = False,
    distinctive_presence_context_lines: LineSelection | None = None,
    spool_dir: str | Path | None = None,
) -> tuple[PresenceRunPlacement, ...] | None:
    """Validate that batch can be safely applied given structural alignment.

    Checks:
    1. File hasn't been completely rewritten (zero alignment)
    2. Missing claimed lines have nearby aligned context
    3. Missing deletion anchors have nearby aligned context
    4. Claimed runs have structurally coherent surrounding context

    Check #4 prevents corruption when applying partial selections.
    If claimed lines come from a source region whose surrounding source structure
    no longer maps coherently into the working tree, inserting those lines may
    preserve incompatible working-tree content that should have been replaced.

    Args:
        line_mapping: Alignment between batch source and working tree
        claimed_lines: Claimed batch source line numbers
        deletions: List of AbsenceClaim objects
        source_lines: Batch source file lines (bytes)
        target_lines: Working tree file lines (bytes)

    Raises:
        MergeError: If structural requirements aren't met
    """
    present_count = sum(
        1 for line in range(1, len(source_lines) + 1)
        if line_mapping.is_source_line_present(line)
    )

    if len(target_lines) == 0:
        return None

    if present_count == 0 and len(target_lines) > 0:
        if claimed_lines:
            first_claimed = _first_selected_line(claimed_lines)
            raise _MergeError(
                _("Cannot reliably place claimed line {line}: file completely rewritten").format(
                    line=first_claimed
                )
            )

    for claimed_line in claimed_lines:
        if claimed_line < 1 or claimed_line > len(source_lines):
            raise _MergeError(
                ngettext(
                    "Claimed line {line} is out of range "
                    "(batch source has {count} line)",
                    "Claimed line {line} is out of range "
                    "(batch source has {count} lines)",
                    len(source_lines),
                ).format(line=claimed_line, count=len(source_lines))
            )

        # The complete-rewrite check above already establishes at least one
        # mapped source line.  Any missing claimed line therefore has mapped
        # context on at least one side; rescanning both sides for every claimed
        # line would add no safety and turns a long missing run quadratic.

    has_unmapped_deletion_anchor = False
    has_unmapped_claimed_deletion_anchor = False
    for deletion in deletions:
        if not deletion.content_lines:
            continue
        after_line = deletion.anchor_line

        if after_line is not None:
            if after_line < 1 or after_line > len(source_lines):
                raise _MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            if not line_mapping.is_source_line_present(after_line):
                has_unmapped_deletion_anchor = True
                if after_line in claimed_lines:
                    has_unmapped_claimed_deletion_anchor = True
                has_context = False
                for check_line in range(
                    max(1, after_line - 3),
                    min(len(source_lines) + 1, after_line + 4),
                ):
                    if (
                        check_line != after_line
                        and line_mapping.is_source_line_present(check_line)
                    ):
                        has_context = True
                        break

                if not has_context and after_line != len(source_lines):
                    raise _MergeError(
                        _("Cannot determine deletion position after line {line}: anchor and neighbors missing").format(
                            line=after_line
                        )
                    )

    # An unclaimed missing deletion anchor will remain absent after presence
    # realization, so let absence handling report its precise anchor error.
    # A claimed anchor can be reintroduced by that realization; validate its
    # placement first so the deletion cannot mask a silent variant interleave.
    if (
        has_unmapped_deletion_anchor
        and not has_unmapped_claimed_deletion_anchor
    ):
        return None

    _missing_presence_lines, presence_placements = (
        _contextual_presence_placements(
            source_lines,
            target_lines,
            claimed_lines,
            line_mapping,
            trusted_source_lines={
                deletion.anchor_line
                for deletion in deletions
                if deletion.anchor_line is not None and deletion.content_lines
            },
            require_distinctive_context=require_distinctive_presence_context,
            distinctive_context_lines=distinctive_presence_context_lines,
            spool_dir=spool_dir,
        )
    )
    # Let absence realization report its more precise missing-anchor error once
    # nearby mapped context has allowed an unmapped deletion anchor through.
    # Presence placement still has to run first: an unrelated missing anchor
    # must not disable ambiguity refusal for every claimed line in the file.
    if has_unmapped_deletion_anchor:
        return presence_placements
    _check_unbounded_trailing_context(
        line_mapping,
        claimed_lines,
        deletions,
        source_lines,
        target_lines,
    )
    return presence_placements


def _check_unbounded_trailing_context(
    line_mapping: LineMapping,
    claimed_lines: LineSelection,
    deletions: list[AbsenceClaim],
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> None:
    """Reject large unmatched tails not resolved by contextual anchors.

    Distinctive anchors choose insertion ordering.  They cannot establish that
    a large, unselected source tail is compatible with unrelated target
    content, so retain the existing conservative protection for that separate
    version-skew shape.
    """
    missing = _mapped_missing_source_lines(
        claimed_lines,
        len(source_lines),
        line_mapping,
    )

    missing_ranges = missing.ranges()
    for cluster in _iter_missing_presence_clusters(missing, line_mapping):
        if cluster.has_locally_collapsed_target_gap():
            continue
        before_source_line = (
            None if cluster.before is None else cluster.before[0]
        )
        before_target_line = (
            None if cluster.before is None else cluster.before[1]
        )
        after_source_line = None if cluster.after is None else cluster.after[0]
        after_target_line = None if cluster.after is None else cluster.after[1]
        deleted_at_terminal_boundary = (
            sum(
                len(deletion.content_lines)
                for deletion in deletions
                if deletion.anchor_line == before_source_line
            )
            if before_target_line is not None and after_target_line is None
            else 0
        )

        for run_index in range(
            cluster.run_start_index,
            cluster.run_stop_index,
        ):
            run_start, run_end = missing_ranges[run_index]
            trailing_gap = (
                after_source_line - run_end - 1
                if after_source_line is not None
                else len(source_lines) - run_end
            )
            if trailing_gap < 3:
                continue

            if before_target_line is not None and after_target_line is not None:
                assert before_source_line is not None
                assert after_source_line is not None
                target_span = after_target_line - before_target_line - 1
                source_span_outside_run = (
                    after_source_line - before_source_line - 1
                    - (run_end - run_start + 1)
                )
                if (
                    target_span < source_span_outside_run
                    or target_span <= run_end - run_start + 1
                ):
                    raise _MergeError(
                        _(
                            "Batch was created from a different version of "
                            "the file"
                        )
                    )
                continue

            if before_target_line is None or after_target_line is not None:
                continue

            target_tail = len(target_lines) - before_target_line
            if (
                target_tail != 0
                and target_tail > deleted_at_terminal_boundary
            ):
                raise _MergeError(
                    _("Batch was created from a different version of the file")
                )


def _first_selected_line(lines: LineSelection) -> int | None:
    first = getattr(lines, "first", None)
    if first is not None:
        return cast(Callable[[], int | None], first)()
    return min(lines) if lines else None
