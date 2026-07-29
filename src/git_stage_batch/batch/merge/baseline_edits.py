"""Baseline-coordinate edit fallback for batch merge."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from .baseline_anchor_matching import (
    baseline_removal_edit as _baseline_removal_edit,
    live_coordinate_edits_are_safe as _live_coordinate_edits_are_safe,
)
from .baseline_edit_plan import (
    BaselineEditPlan as _BaselineEditPlan,
    BaselineEditStream as _BaselineEditStream,
)
from .baseline_reference_positions import (
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .baseline_replacement_edits import (
    plan_replacement_unit_edits as _plan_replacement_unit_edits,
    replacement_source_ranges_fit_presence as _replacement_source_ranges_fit_presence,
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
from .candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim


_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


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
    target_spans: Sequence[tuple[int, ...]],
    *,
    positioned_lines_are_ordered: bool,
    trust_baseline_coordinates: bool,
) -> bool:
    """Append required insertion groups and retain only their source records."""
    if not positioned_lines_are_ordered:
        sort_mapped_records(positioned_lines)

    group_start = 0
    retained_line_count = 0
    target_span_index = 0
    while group_start < len(positioned_lines):
        position = positioned_lines[group_start][0]
        group_stop = group_start + 1
        while (
            group_stop < len(positioned_lines)
            and positioned_lines[group_stop][0] == position
        ):
            group_stop += 1

        while (
            target_span_index < len(target_spans)
            and target_spans[target_span_index][1] <= position
        ):
            target_span_index += 1

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

        retain_group = not group_matches
        if group_matches:
            group_end = position + group_stop - group_start
            removed_line_count = 0
            scan_index = target_span_index
            while (
                scan_index < len(target_spans)
                and target_spans[scan_index][0] < group_end
            ):
                span_start, span_end = target_spans[scan_index]
                removed_line_count += max(
                    0,
                    min(group_end, span_end) - max(position, span_start),
                )
                if span_end >= group_end:
                    break
                scan_index += 1

            if 0 < removed_line_count < group_end - position:
                return False
            retain_group = removed_line_count == group_end - position

        if retain_group:
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
    return True


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
    target_spans = plan.sorted_target_spans()
    if target_spans is None:
        return None
    if not _add_positioned_presence_insertions(
        plan,
        source_lines,
        working_lines,
        positioned_lines,
        target_spans,
        positioned_lines_are_ordered=positioned_lines_are_ordered,
        trust_baseline_coordinates=trust_baseline_coordinates,
    ):
        return None

    return positioned_lines


def _has_complete_baseline_references(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
) -> bool:
    for claimed_line in presence_line_set:
        reference = ownership.presence_baseline_reference(claimed_line)
        if reference is None or not reference.has_after_line:
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not reference.has_after_line:
            return False
    return bool(presence_line_set or deletion_claims)


def has_recorded_baseline_coordinates(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether selected edit metadata includes a recorded coordinate."""
    for presence_claim in ownership.presence_claims:
        for claimed_line, presence_reference in (
            presence_claim.baseline_references.items()
        ):
            if (
                claimed_line in presence_line_set
                and presence_reference.has_after_line
            ):
                return True
    for deletion_claim in deletion_claims:
        deletion_reference = deletion_claim.baseline_reference
        if (
            deletion_reference is not None
            and deletion_reference.has_after_line
        ):
            return True
    for unit in ownership.replacement_units:
        origin = unit.origin
        if origin is None:
            continue
        origin_reference = origin.baseline_reference
        if origin_reference is not None and origin_reference.has_after_line:
            return True
    return False


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
