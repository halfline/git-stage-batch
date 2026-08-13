"""Presence edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from .baseline_edit_plan import BaselineEditPlan
from .baseline_reference_positions import (
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines as _match_lines
from ..line_matching.match_workspace import MatcherWorkspace

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership


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


def _collect_presence_position_records(
    workspace: MatcherWorkspace,
    source_line_count: int,
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
    *,
    prefer_source_mapping: bool,
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
        position = (
            None
            if prefer_source_mapping
            else _find_baseline_insertion_position(
                ownership.presence_baseline_reference(claimed_line),
                working_lines,
            )
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
    plan: BaselineEditPlan,
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    unmapped_lines: MappedRecordVector,
    *,
    allow_adjacent_insertion: bool,
    source_to_working_mapping: LineMapping | None,
    spool_dir: str | Path | None,
) -> bool:
    """Resolve unpositioned lines through mapping and adjacent mapped anchors."""
    if not unmapped_lines:
        return True

    if not plan.sort_target_spans_and_validate():
        return False

    target_lines_are_ordered = True
    previous_target_line: int | None = None
    owned_mapping = None
    mapping = source_to_working_mapping
    can_insert_adjacent = allow_adjacent_insertion and mapping is not None
    if mapping is None:
        owned_mapping = _match_lines(
            source_lines,
            working_lines,
            spool_dir=spool_dir,
        )
        mapping = owned_mapping
    adjacent_insertions = workspace.record_vector(
        len(unmapped_lines),
        "QQ",
    )
    retained_target_count = 0
    source_scan = 1
    latest_mapped_source: int | None = None
    latest_mapped_target: int | None = None
    previous_missing_source: int | None = None
    current_insertion_position: int | None = None
    try:
        for record_index in range(len(unmapped_lines)):
            claimed_line = unmapped_lines[record_index][0]
            while source_scan < claimed_line:
                target_line = mapping.get_target_line_from_source_line(
                    source_scan
                )
                if target_line is not None:
                    latest_mapped_source = source_scan
                    latest_mapped_target = target_line
                source_scan += 1

            target_line = mapping.get_target_line_from_source_line(claimed_line)
            if target_line is None:
                if not can_insert_adjacent:
                    return False
                continues_missing_run = (
                    previous_missing_source is not None
                    and claimed_line == previous_missing_source + 1
                )
                if not continues_missing_run:
                    if (
                        latest_mapped_source != claimed_line - 1
                        or latest_mapped_target is None
                    ):
                        return False
                    current_insertion_position = latest_mapped_target
                assert current_insertion_position is not None
                adjacent_insertions.append((
                    current_insertion_position,
                    claimed_line,
                ))
                previous_missing_source = claimed_line
                source_scan = claimed_line + 1
                continue

            target_index = target_line - 1
            if previous_target_line is not None and target_index < previous_target_line:
                target_lines_are_ordered = False
            unmapped_lines[retained_target_count] = (target_index,)
            retained_target_count += 1
            previous_target_line = target_index
            latest_mapped_source = claimed_line
            latest_mapped_target = target_line
            previous_missing_source = None
            current_insertion_position = None
            source_scan = claimed_line + 1
    finally:
        if owned_mapping is not None:
            owned_mapping.close()

    unmapped_lines.truncate(retained_target_count)
    if not target_lines_are_ordered:
        sort_mapped_records(unmapped_lines)
    if plan.removes_any_target_lines(unmapped_lines):
        return False

    insertion_start = 0
    while insertion_start < len(adjacent_insertions):
        insertion_position = adjacent_insertions[insertion_start][0]
        insertion_stop = insertion_start + 1
        while (
            insertion_stop < len(adjacent_insertions)
            and adjacent_insertions[insertion_stop][0] == insertion_position
        ):
            insertion_stop += 1
        plan.add_positioned_source_lines(
            insertion_position,
            adjacent_insertions,
            insertion_start,
            insertion_stop,
        )
        insertion_start = insertion_stop
    workspace.close_resource(adjacent_insertions)
    return True


def _add_positioned_presence_insertions(
    plan: BaselineEditPlan,
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


def plan_presence_insertions(
    plan: BaselineEditPlan,
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
    *,
    allow_adjacent_unmapped_presence: bool,
    prefer_source_mapping: bool,
    trust_baseline_coordinates: bool,
    source_to_working_mapping: LineMapping | None,
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
        prefer_source_mapping=prefer_source_mapping,
    )
    if position_records is None:
        return None
    positioned_lines, unmapped_lines, positioned_lines_are_ordered = (
        position_records
    )

    if not _mapping_preserves_unpositioned_presence(
        plan,
        workspace,
        source_lines,
        working_lines,
        unmapped_lines,
        allow_adjacent_insertion=allow_adjacent_unmapped_presence,
        source_to_working_mapping=source_to_working_mapping,
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
