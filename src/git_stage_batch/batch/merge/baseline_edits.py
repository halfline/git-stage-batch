"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_edit_plan import (
    BaselineEditPlan as _BaselineEditPlan,
    BaselineEditStream as _BaselineEditStream,
)
from .baseline_reference_positions import (
    baseline_reference_absence_position as _find_baseline_absence_position,
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .baseline_replacement_choices import (
    replacement_origin_choices_for_unit as _replacement_origin_choices_for_unit,
)
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    replacement_source_range_capacity as _replacement_source_range_capacity,
    selected_replacement_source_ranges as _selected_replacement_source_ranges,
)
from ..line_matching.sequence_equality import (
    line_sequences_equal as _line_sequences_match,
    line_slice_equals as _line_slice_matches,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines as _match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload as _reference_line_payload,
)
from .candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.references import BaselineReference
    from ..ownership.replacement_units import (
        ReplacementUnit,
        ReplacementUnitOrigin,
    )


_BaselineRemovalEdit = tuple[int, int]
_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


@contextmanager
def acquire_deletion_anchor_pairs_for_target(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    *,
    trust_baseline_coordinates: bool = False,
    spool_dir: str | Path | None = None,
) -> Iterator[Sequence[tuple[int, int]]]:
    """Return coherent source-to-target anchors recorded by deletion claims.

    Exact baseline realization may trust recorded coordinates after checking
    the deleted bytes. Live targets additionally require the stored boundary
    identity to match before a coordinate can constrain structural alignment.
    """
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        anchor_pairs = workspace.record_vector(
            len(deletion_claims),
            "QQ",
        )
        occurrence_index: LinePayloadOccurrenceIndex | None = None
        for claim in deletion_claims:
            source_line = claim.anchor_line
            reference = claim.baseline_reference
            target_line = None if reference is None else reference.after_line
            if type(source_line) is not int or type(target_line) is not int:
                continue
            if source_line < 1 or source_line > len(source_lines):
                continue
            if target_line < 1 or target_line > len(target_lines):
                continue
            if source_lines[source_line - 1] != target_lines[target_line - 1]:
                continue

            if trust_baseline_coordinates:
                forbidden_sequence = normalize_line_sequence_endings(
                    claim.content_lines
                )
                if not forbidden_sequence or not _line_slice_matches(
                    target_lines,
                    target_line,
                    forbidden_sequence,
                ):
                    continue
            else:
                removal_edit = _baseline_removal_edit(claim, target_lines)
                if removal_edit is None:
                    continue
                if (
                    not _removal_boundary_is_fixed_to_file_edge(claim)
                    and occurrence_index is None
                ):
                    occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        target_lines,
                    )
                if not _live_removal_boundary_is_unique(
                    claim,
                    target_lines,
                    removal_edit[0],
                    occurrence_index,
                ):
                    continue

            anchor_pairs.append((source_line, target_line))

        sort_mapped_records(anchor_pairs)
        previous_pair: tuple[int, int] | None = None
        previous_source = 0
        previous_target = 0
        anchor_count = 0
        for pair in anchor_pairs:
            if pair == previous_pair:
                continue
            source_line, target_line = pair
            if source_line <= previous_source or target_line <= previous_target:
                anchor_pairs.truncate(0)
                yield anchor_pairs
                return
            anchor_pairs[anchor_count] = pair
            anchor_count += 1
            previous_pair = pair
            previous_source = source_line
            previous_target = target_line

        anchor_pairs.truncate(anchor_count)
        yield anchor_pairs


def _removal_boundary_is_fixed_to_file_edge(claim: AbsenceClaim) -> bool:
    """Return whether a boundary marker admits only one target position."""
    reference = claim.baseline_reference
    return reference is not None and (
        reference.after_line is None
        or (
            reference.has_before_line
            and reference.before_line is None
        )
    )


def _baseline_removal_edit(
    claim: AbsenceClaim,
    working_lines: Sequence[bytes],
) -> _BaselineRemovalEdit | None:
    if not claim.content_lines:
        return None

    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    position = _find_baseline_absence_position(
        claim.baseline_reference,
        working_lines,
        len(forbidden_sequence),
    )
    if position is None:
        return None
    if not _line_slice_matches(working_lines, position, forbidden_sequence):
        return None
    return position, position + len(forbidden_sequence)


def _removal_boundary_identity_matches_at(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    position: int,
    forbidden_sequence: Sequence[bytes],
) -> bool:
    """Return whether one target position has the claim's full identity."""
    reference = claim.baseline_reference
    if reference is None or not reference.has_after_line:
        return False
    if not _line_slice_matches(target_lines, position, forbidden_sequence):
        return False

    if reference.after_line is None:
        if position != 0:
            return False
    else:
        if position == 0 or reference.after_content is None:
            return False
        if _reference_line_payload(target_lines[position - 1]) != (
            _reference_line_payload(reference.after_content)
        ):
            return False

    if not reference.has_before_line:
        return True

    before_position = position + len(forbidden_sequence)
    if reference.before_line is None:
        return before_position == len(target_lines)
    if before_position >= len(target_lines) or reference.before_content is None:
        return False
    return _reference_line_payload(target_lines[before_position]) == (
        _reference_line_payload(reference.before_content)
    )


def _boundary_identity_occurs_once(
    occurrence_index: LinePayloadOccurrenceIndex,
    expected_position: int,
    last_position: int,
    *,
    after_content: bytes | None,
    span_contents: Sequence[bytes],
    before_content: bytes | None,
    before_delta: int,
    identity_matches_at: Callable[[int], bool],
) -> bool:
    """Return whether indexed boundary content identifies one full identity."""
    rarest_content: bytes | None = None
    rarest_boundary_delta = 0
    rarest_count: int | None = None

    def consider(content: bytes | None, boundary_delta: int) -> None:
        nonlocal rarest_content
        nonlocal rarest_boundary_delta
        nonlocal rarest_count
        if content is None:
            return
        count = occurrence_index.occurrence_count(content)
        if rarest_count is None or count < rarest_count:
            rarest_content = content
            rarest_boundary_delta = boundary_delta
            rarest_count = count

    consider(after_content, 1)
    for offset, content in enumerate(span_contents):
        consider(content, -offset)
    consider(before_content, before_delta)

    if rarest_content is None or rarest_count is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > last_position
            or position == expected_position
        ):
            continue
        if identity_matches_at(position):
            return False
    return True


def _live_removal_boundary_is_unique(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex | None,
) -> bool:
    """Require a live deletion boundary to identify one target occurrence."""
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    if not forbidden_sequence or not _removal_boundary_identity_matches_at(
        claim,
        target_lines,
        expected_position,
        forbidden_sequence,
    ):
        return False
    if _removal_boundary_is_fixed_to_file_edge(claim):
        return True
    if occurrence_index is None:
        return False

    reference = claim.baseline_reference
    assert reference is not None
    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - len(forbidden_sequence),
        after_content=(
            reference.after_content if reference.after_line is not None else None
        ),
        span_contents=forbidden_sequence,
        before_content=(
            reference.before_content
            if reference.has_before_line and reference.before_line is not None
            else None
        ),
        before_delta=-len(forbidden_sequence),
        identity_matches_at=lambda position: _removal_boundary_identity_matches_at(
            claim,
            target_lines,
            position,
            forbidden_sequence,
        ),
    )


def _insertion_boundary_identity_matches_at(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one insertion position has the reference's full identity."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return False

    after_line = getattr(reference, "after_line", None)
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = getattr(reference, "after_content", None)
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        if before_line is None:
            return position == len(target_lines)
        before_content = getattr(reference, "before_content", None)
        return (
            position < len(target_lines)
            and before_content is not None
            and _reference_line_payload(target_lines[position])
            == _reference_line_payload(before_content)
        )

    return position == len(target_lines)


def _live_insertion_boundary_is_unique(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require an insertion reference to identify one live target boundary."""
    if not _insertion_boundary_identity_matches_at(
        reference,
        target_lines,
        expected_position,
    ):
        return False

    after_line = getattr(reference, "after_line", None)
    before_line = getattr(reference, "before_line", None)
    if after_line is None or (
        getattr(reference, "has_before_line", False)
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines),
        after_content=(
            getattr(reference, "after_content", None)
            if after_line is not None
            else None
        ),
        span_contents=(),
        before_content=(
            getattr(reference, "before_content", None)
            if (
                getattr(reference, "has_before_line", False) and before_line is not None
            )
            else None
        ),
        before_delta=0,
        identity_matches_at=lambda position: _insertion_boundary_identity_matches_at(
            reference,
            target_lines,
            position,
        ),
    )


def _replacement_origin_boundary_identity_matches_at(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one target span has the replacement parent's identity."""
    reference = getattr(origin, "baseline_reference", None)
    old_line_count = getattr(origin, "old_line_count", None)
    if (
        reference is None
        or not getattr(reference, "has_after_line", False)
        or type(old_line_count) is not int
        or old_line_count <= 0
        or position < 0
        or position + old_line_count > len(target_lines)
    ):
        return False

    after_line = getattr(reference, "after_line", None)
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = getattr(reference, "after_content", None)
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if not getattr(reference, "has_before_line", False):
        return True

    before_line = getattr(reference, "before_line", None)
    before_position = position + old_line_count
    if before_line is None:
        return before_position == len(target_lines)
    before_content = getattr(reference, "before_content", None)
    return (
        before_position < len(target_lines)
        and before_content is not None
        and _reference_line_payload(target_lines[before_position])
        == _reference_line_payload(before_content)
    )


def _live_replacement_origin_boundary_is_unique(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require a replacement parent to identify one live target span."""
    if origin is None or not _replacement_origin_boundary_identity_matches_at(
        origin,
        target_lines,
        expected_position,
    ):
        return False

    reference = origin.baseline_reference
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - origin.old_line_count,
        after_content=(reference.after_content if after_line is not None else None),
        span_contents=(),
        before_content=(
            reference.before_content
            if reference.has_before_line and before_line is not None
            else None
        ),
        before_delta=-origin.old_line_count,
        identity_matches_at=lambda position: (
            _replacement_origin_boundary_identity_matches_at(
                origin,
                target_lines,
                position,
            )
        ),
    )


def _live_coordinate_edits_are_safe(
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: Sequence[tuple[int, ...]],
    positioned_insertion_lines: Sequence[tuple[int, ...]] | None,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Return whether every coordinate edit has one live target boundary."""
    if not deletion_claims and not positioned_insertion_lines:
        return True

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(
            workspace,
            working_lines,
        )
        replacement_reference_count = sum(
            len(unit.deletion_indices)
            for unit in ownership.replacement_units
        )
        replacement_units_by_deletion = workspace.record_vector(
            replacement_reference_count,
            "QQ",
        )
        for unit_index, unit in enumerate(ownership.replacement_units):
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is int
                    and 0 <= deletion_index < len(deletion_claims)
                ):
                    replacement_units_by_deletion.append((
                        deletion_index,
                        unit_index,
                    ))
        sort_mapped_records(replacement_units_by_deletion)

        replacement_reference_index = 0
        for deletion_index, claim in enumerate(deletion_claims):
            (
                has_actual_bounds,
                actual_start,
                actual_end,
                coordinate_was_reviewed,
            ) = (
                deletion_edit_bounds[deletion_index]
            )
            if not has_actual_bounds:
                return False
            actual_bounds = (actual_start, actual_end)
            while (
                replacement_reference_index
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    replacement_reference_index
                ][0] < deletion_index
            ):
                replacement_reference_index += 1
            next_replacement_reference = replacement_reference_index
            while (
                next_replacement_reference
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    next_replacement_reference
                ][0] == deletion_index
            ):
                next_replacement_reference += 1
            if coordinate_was_reviewed:
                replacement_reference_index = next_replacement_reference
                continue

            removal_edit = _baseline_removal_edit(claim, working_lines)
            if (
                removal_edit is not None
                and actual_bounds == removal_edit[:2]
                and _live_removal_boundary_is_unique(
                    claim,
                    working_lines,
                    removal_edit[0],
                    occurrence_index,
                )
            ):
                replacement_reference_index = next_replacement_reference
                continue

            origin_is_safe = False
            for reference_index in range(
                replacement_reference_index,
                next_replacement_reference,
            ):
                unit_index = replacement_units_by_deletion[reference_index][1]
                unit = ownership.replacement_units[unit_index]
                origin = getattr(unit, "origin", None)
                parent_bounds = _replacement_origin_absence_bounds(
                    origin,
                    working_lines,
                )
                if (
                    parent_bounds is not None
                    and parent_bounds[0] <= actual_bounds[0]
                    and actual_bounds[1] <= parent_bounds[1]
                    and _live_replacement_origin_boundary_is_unique(
                        origin,
                        working_lines,
                        parent_bounds[0],
                        occurrence_index,
                    )
                ):
                    origin_is_safe = True
                    break
            if not origin_is_safe:
                return False
            replacement_reference_index = next_replacement_reference

        if positioned_insertion_lines is not None:
            for position, claimed_line in positioned_insertion_lines:
                reference = ownership.presence_baseline_reference(
                    claimed_line
                )
                if not _live_insertion_boundary_is_unique(
                    reference,
                    working_lines,
                    position,
                    occurrence_index,
                ):
                    return False

    return True


def _replacement_origin_absence_bounds(
    origin: ReplacementUnitOrigin | None,
    working_lines: Sequence[bytes],
) -> tuple[int, int] | None:
    """Return the target bounds of an original replacement parent, if provable."""
    if origin is None or getattr(origin, "baseline_reference", None) is None:
        return None
    old_line_count = getattr(origin, "old_line_count", None)
    if type(old_line_count) is not int or old_line_count <= 0:
        return None

    position = _find_baseline_absence_position(
        origin.baseline_reference,
        working_lines,
        old_line_count,
    )
    if position is None:
        return None
    return position, position + old_line_count


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

    old_line_count = getattr(origin, "old_line_count", None)
    new_start = getattr(origin, "new_start", None)
    new_end = getattr(origin, "new_end", None)
    if (
        type(old_line_count) is not int
        or type(new_start) is not int
        or type(new_end) is not int
        or old_line_count <= 0
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
    origin_reference = getattr(origin, "baseline_reference", None)
    if (
        claim_reference is not None
        and getattr(claim_reference, "has_after_line", False)
        and origin_reference is not None
        and getattr(origin_reference, "has_after_line", False)
    ):
        relative_offset = (
            (getattr(claim_reference, "after_line", None) or 0)
            - (getattr(origin_reference, "after_line", None) or 0)
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


def _positioned_source_lines_match(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    position: int,
    positioned_lines: Sequence[tuple[int, ...]],
    start_index: int,
    stop_index: int,
) -> bool:
    """Return whether one positioned source-line group already exists."""
    line_count = stop_index - start_index
    if position < 0 or position + line_count > len(working_lines):
        return False

    for offset, record_index in enumerate(range(start_index, stop_index)):
        _record_position, source_line = positioned_lines[record_index]
        if working_lines[position + offset] != source_lines[source_line - 1]:
            return False
    return True


def _plan_replacement_unit_edits(
    workspace: MatcherWorkspace,
    plan: _BaselineEditPlan,
    source_line_count: int,
    working_lines: Sequence[bytes],
    replacement_units: Sequence[ReplacementUnit],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    replacement_source_ranges: MappedRecordVector,
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
) -> bool:
    """Plan coupled replacement units and record their claimed source ranges."""
    for unit_index, unit in enumerate(replacement_units):
        claimed_ranges = _collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        if (
            claimed_ranges is None
            or not claimed_ranges
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

        replacement_edit = _replacement_baseline_edit(
            deletion_claims[deletion_index],
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
            ((source_start, source_end) for source_start, source_end in claimed_ranges),
        )
        for source_start, source_end in claimed_ranges:
            replacement_source_ranges.append((source_start, source_end))
        deletion_edit_bounds[deletion_index] = (
            1,
            start,
            end,
            coordinate_was_reviewed,
        )
        workspace.close_resource(claimed_ranges)

    return True


def _replacement_source_ranges_fit_presence(
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


def _presence_lines_without_replacements(
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
) -> Iterator[int]:
    """Yield claimed source lines not carried by replacement units."""
    replacement_range_index = 0
    for presence_start, presence_end in presence_lines.ranges():
        remaining_start = presence_start
        while (
            replacement_range_index < len(replacement_source_ranges)
            and replacement_source_ranges[replacement_range_index][0] <= presence_end
        ):
            replacement_start, replacement_end = replacement_source_ranges[
                replacement_range_index
            ]
            for claimed_line in range(remaining_start, replacement_start):
                yield claimed_line
            remaining_start = replacement_end + 1
            replacement_range_index += 1
        for claimed_line in range(remaining_start, presence_end + 1):
            yield claimed_line


def _plan_independent_removal_edits(
    plan: _BaselineEditPlan,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
) -> bool:
    """Plan removals not already coupled to replacement units."""
    for deletion_index, claim in enumerate(deletion_claims):
        if deletion_edit_bounds[deletion_index][0]:
            continue

        removal_edit = _baseline_removal_edit(claim, working_lines)
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


def _collect_presence_position_records(
    workspace: MatcherWorkspace,
    source_line_count: int,
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
) -> tuple[MappedRecordVector, MappedRecordVector, bool] | None:
    """Partition presence lines into positioned and mapping-backed records."""
    positioned_lines = workspace.record_vector(
        len(presence_lines),
        "QQ",
    )
    unmapped_lines = workspace.record_vector(
        len(presence_lines),
        "Q",
    )
    positioned_lines_are_ordered = True
    previous_position: int | None = None

    for claimed_line in _presence_lines_without_replacements(
        presence_lines,
        replacement_source_ranges,
    ):
        if claimed_line > source_line_count:
            return None
        position = _find_baseline_insertion_position(
            ownership.presence_baseline_reference(claimed_line),
            working_lines,
        )
        if position is None:
            unmapped_lines.append((claimed_line,))
        else:
            if (
                previous_position is not None
                and position < previous_position
            ):
                positioned_lines_are_ordered = False
            positioned_lines.append((position, claimed_line))
            previous_position = position

    return positioned_lines, unmapped_lines, positioned_lines_are_ordered


def _mapping_preserves_unpositioned_presence(
    plan: _BaselineEditPlan,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    unmapped_lines: MappedRecordVector,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Return whether mapped unpositioned lines survive planned removals."""
    if not unmapped_lines:
        return True

    if not plan.sort_target_spans_and_validate():
        return False

    target_lines_are_ordered = True
    previous_target_line: int | None = None
    with _match_lines(
        source_lines,
        working_lines,
        spool_dir=spool_dir,
    ) as mapping:
        for record_index in range(len(unmapped_lines)):
            claimed_line = unmapped_lines[record_index][0]
            target_line = mapping.get_target_line_from_source_line(claimed_line)
            if target_line is None:
                return False
            target_index = target_line - 1
            if previous_target_line is not None and target_index < previous_target_line:
                target_lines_are_ordered = False
            unmapped_lines[record_index] = (target_index,)
            previous_target_line = target_index

    if not target_lines_are_ordered:
        sort_mapped_records(unmapped_lines)
    return not plan.removes_any_target_lines(unmapped_lines)


def _add_positioned_presence_insertions(
    plan: _BaselineEditPlan,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    positioned_lines: MappedRecordVector,
    *,
    positioned_lines_are_ordered: bool,
    trust_baseline_coordinates: bool,
) -> None:
    """Append required insertion groups and retain only their source records."""
    if not positioned_lines_are_ordered:
        sort_mapped_records(positioned_lines)

    group_start = 0
    retained_line_count = 0
    while group_start < len(positioned_lines):
        position = positioned_lines[group_start][0]
        group_stop = group_start + 1
        while (
            group_stop < len(positioned_lines)
            and positioned_lines[group_stop][0] == position
        ):
            group_stop += 1

        group_matches = (
            not trust_baseline_coordinates
            and _positioned_source_lines_match(
                source_lines,
                working_lines,
                position,
                positioned_lines,
                group_start,
                group_stop,
            )
        )

        if not group_matches:
            plan.add_positioned_source_lines(
                position,
                positioned_lines,
                group_start,
                group_stop,
            )
            for record_index in range(group_start, group_stop):
                positioned_lines[retained_line_count] = positioned_lines[record_index]
                retained_line_count += 1
        group_start = group_stop
    positioned_lines.truncate(retained_line_count)


def _plan_presence_insertions(
    plan: _BaselineEditPlan,
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
    *,
    trust_baseline_coordinates: bool,
    spool_dir: str | Path | None,
) -> MappedRecordVector | None:
    """Plan explicit insertions and validate presence resolved by matching."""
    position_records = _collect_presence_position_records(
        workspace,
        len(source_lines),
        working_lines,
        ownership,
        presence_lines,
        replacement_source_ranges,
    )
    if position_records is None:
        return None
    positioned_lines, unmapped_lines, positioned_lines_are_ordered = (
        position_records
    )

    if not _mapping_preserves_unpositioned_presence(
        plan,
        source_lines,
        working_lines,
        unmapped_lines,
        spool_dir=spool_dir,
    ):
        return None

    workspace.close_resource(unmapped_lines)
    _add_positioned_presence_insertions(
        plan,
        source_lines,
        working_lines,
        positioned_lines,
        positioned_lines_are_ordered=positioned_lines_are_ordered,
        trust_baseline_coordinates=trust_baseline_coordinates,
    )

    return positioned_lines


def _has_complete_baseline_references(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
) -> bool:
    for claimed_line in presence_line_set:
        reference = ownership.presence_baseline_reference(claimed_line)
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    return bool(presence_line_set or deletion_claims)


def _all_deletions_are_already_absent(
    deletion_claims: Sequence[AbsenceClaim],
    working_lines: Sequence[bytes],
) -> bool:
    """Return whether baseline and source anchors prove removals satisfied."""
    for claim in deletion_claims:
        if not claim.content_lines:
            continue
        if _baseline_removal_edit(claim, working_lines) is not None:
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

        if _line_slice_matches(
            working_lines,
            source_position,
            forbidden_sequence,
        ):
            return False

    return True


def try_apply_baseline_replacement_units(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    *,
    resolution: _MergeResolution | None = None,
    max_resolution_choices: int = _DEFAULT_RESOLUTION_CHOICE_LIMIT,
    trust_baseline_coordinates: bool = False,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes] | None:
    """Apply baseline-coordinate edits when structural source anchors fail.

    This is a conservative fallback for same-source round trips where the batch
    source is the post-change file and the target is still the pre-change
    baseline/index. In that shape, source anchors can legitimately be absent
    even though the old baseline bytes still exist at an exact recorded
    coordinate.
    """
    if _selection_outside_bounds(presence_line_set, len(source_lines)):
        return None

    if _line_sequences_match(
        source_lines, working_lines
    ) and _has_complete_baseline_references(
        ownership,
        presence_line_set,
        deletion_claims,
    ) and _all_deletions_are_already_absent(
        deletion_claims,
        working_lines,
    ):
        return iter(working_lines)

    workspace = MatcherWorkspace(spool_dir=spool_dir)
    try:
        deletion_edit_bounds = workspace.record_vector(
            len(deletion_claims),
            "QQQQ",
            length=len(deletion_claims),
        )
        plan = _build_baseline_edit_plan(
            workspace,
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            deletion_edit_bounds,
            resolution=resolution,
            max_resolution_choices=max_resolution_choices,
            trust_baseline_coordinates=trust_baseline_coordinates,
            spool_dir=spool_dir,
        )
        if plan is None:
            workspace.close()
            return None

        workspace.close_resource(deletion_edit_bounds)
        if not plan:
            workspace.close()
            return iter(working_lines)

        return _BaselineEditStream(
            plan,
            source_lines,
            working_lines,
            workspace,
        )
    except BaseException:
        workspace.close()
        raise


def _build_baseline_edit_plan(
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    *,
    resolution: _MergeResolution | None,
    max_resolution_choices: int,
    trust_baseline_coordinates: bool,
    spool_dir: str | Path | None,
) -> _BaselineEditPlan | None:
    """Build and validate one storage-backed exact-coordinate edit plan."""
    replacement_units = getattr(ownership, "replacement_units", [])
    presence_lines = coerce_line_ranges(presence_line_set)
    replacement_source_range_capacity = sum(
        _replacement_source_range_capacity(unit.presence_lines)
        for unit in replacement_units
    )
    plan = _BaselineEditPlan(
        workspace,
        edit_capacity=(
            len(replacement_units)
            + len(deletion_claims)
            + len(presence_lines)
        ),
        source_range_capacity=(
            replacement_source_range_capacity + len(presence_lines)
        ),
    )
    replacement_source_ranges = workspace.record_vector(
        replacement_source_range_capacity,
        "QQ",
    )
    if not _plan_replacement_unit_edits(
        workspace,
        plan,
        len(source_lines),
        working_lines,
        replacement_units,
        deletion_claims,
        deletion_edit_bounds,
        replacement_source_ranges,
        resolution,
        max_resolution_choices=max_resolution_choices,
    ):
        return None
    if not _replacement_source_ranges_fit_presence(
        presence_lines,
        replacement_source_ranges,
    ):
        return None
    if not _plan_independent_removal_edits(
        plan,
        working_lines,
        deletion_claims,
        deletion_edit_bounds,
    ):
        return None

    positioned_insertion_lines = _plan_presence_insertions(
        plan,
        workspace,
        source_lines,
        working_lines,
        ownership,
        presence_lines,
        replacement_source_ranges,
        trust_baseline_coordinates=trust_baseline_coordinates,
        spool_dir=spool_dir,
    )
    if positioned_insertion_lines is None:
        return None

    workspace.close_resource(replacement_source_ranges)
    if (
        not trust_baseline_coordinates
        and not _live_coordinate_edits_are_safe(
            ownership,
            working_lines,
            deletion_claims,
            deletion_edit_bounds,
            positioned_insertion_lines,
            spool_dir=spool_dir,
        )
    ):
        return None

    workspace.close_resource(positioned_insertion_lines)
    if not plan.sort_and_validate():
        return None
    return plan


def has_missing_origin_replacement_claims(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    source_lines: Sequence[bytes],
    mapping: LineMapping,
    *,
    spool_dir: str | Path | None = None,
) -> bool:
    """Return whether parent-tracked replacement lines would need placement."""
    selected_presence = coerce_line_ranges(presence_line_set)
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        for unit in getattr(ownership, "replacement_units", []):
            if getattr(unit, "origin", None) is None:
                continue
            claimed_ranges = _collect_replacement_source_ranges(
                workspace,
                unit.presence_lines,
            )
            if claimed_ranges is None:
                return True
            try:
                for claimed_start, claimed_end in _selected_replacement_source_ranges(
                    claimed_ranges,
                    selected_presence,
                ):
                    for claimed_line in range(claimed_start, claimed_end + 1):
                        if claimed_line > len(source_lines):
                            continue
                        if (
                            mapping.get_target_line_from_source_line(claimed_line)
                            is None
                        ):
                            return True
            finally:
                workspace.close_resource(claimed_ranges)
    return False
