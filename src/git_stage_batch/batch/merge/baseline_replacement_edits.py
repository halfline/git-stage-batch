"""Record replacement ranges and verify their mapped-source boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import BaselineRemovalEdit as _BaselineRemovalEdit
from .baseline_edit_plan import BaselineEditPlan
from .baseline_reference_positions import (
    baseline_reference_insertion_position as _baseline_reference_insertion_position,
)
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
)
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.sequence_equality import line_slice_equals as _line_slice_matches
from .replacement_group_planning import (
    _replacement_group_old_side_matches_target,
    _trusted_mapped_replacement_group_bounds,
)
from .replacement_edit_locations import (
    _replacement_edit_from_trusted_target,
    _replacement_edit_with_origin_guard,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.replacement_units import ReplacementUnit
    from .presence_reference_index import EffectivePresenceReferenceIndex


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


def _plan_relocated_replacement_from_presence_reference(
    plan: BaselineEditPlan,
    claim: AbsenceClaim,
    unit: ReplacementUnit,
    claimed_ranges: Sequence[tuple[int, ...]],
    working_lines: Sequence[bytes],
    presence_references: EffectivePresenceReferenceIndex | None,
) -> _BaselineRemovalEdit | None:
    """Move a replacement when every saved line records one newer boundary."""
    if unit.origin is None or presence_references is None:
        return None
    reference = presence_references.common_reference_for_ranges(claimed_ranges)
    insertion_position = _baseline_reference_insertion_position(
        reference,
        working_lines,
    )
    removal_edit = _replacement_edit_with_origin_guard(
        claim,
        unit.origin,
        working_lines,
    )
    if insertion_position is None or removal_edit is None:
        return None

    removal_start, removal_end = removal_edit
    if (
        insertion_position == removal_start
        or removal_start < insertion_position < removal_end
    ):
        return None
    plan.add_removal(removal_start, removal_end)
    plan.add_source_ranges(
        insertion_position,
        insertion_position,
        ((source_start, source_end) for source_start, source_end in claimed_ranges),
    )
    return removal_edit


def _mapped_source_alternative_edit(
    claim: AbsenceClaim,
    claimed_ranges: Sequence[tuple[int, ...]],
    source_lines: Sequence[bytes] | None,
    mapping: LineMapping | None,
) -> _BaselineRemovalEdit | None:
    """Find the target span for the neighboring live version."""
    if (
        not claim.source_alternative
        or source_lines is None
        or mapping is None
        or len(claimed_ranges) != 1
    ):
        return None
    alternative_lines = normalize_line_sequence_endings(claim.content_lines)
    if not alternative_lines:
        return None
    alternative_source_start = claimed_ranges[0][1] + 1
    alternative_source_end = alternative_source_start + len(alternative_lines) - 1
    if alternative_source_end > len(source_lines):
        return None

    target_start: int | None = None
    for offset, expected_line in enumerate(alternative_lines):
        source_line = alternative_source_start + offset
        target_line = mapping.get_target_line_from_source_line(source_line)
        if (
            source_lines[source_line - 1] != expected_line
            or target_line is None
            or mapping.get_source_line_from_target_line(target_line) != source_line
            or (target_start is not None and target_line != target_start + offset + 1)
        ):
            return None
        if target_start is None:
            target_start = target_line - 1
    assert target_start is not None
    return target_start, target_start + len(alternative_lines)


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
