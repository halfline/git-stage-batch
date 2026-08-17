"""Presence edits for baseline-coordinate merge planning."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from .baseline_anchor_matching import (
    _insertion_boundary_identity_matches_at,
    unique_live_insertion_boundary_position,
)
from .baseline_edit_plan import BaselineEditPlan
from .baseline_reference_positions import (
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .presence_reference_index import EffectivePresenceReferenceIndex
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines as _match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)

if TYPE_CHECKING:
    from ..ownership.references import BaselineReference


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


def _positioned_group_has_mapped_run(
    mapping: LineMapping,
    positioned_lines: Sequence[tuple[int, ...]],
    group_start: int,
    group_stop: int,
) -> bool:
    """Return whether structural matching retained adjacent claimed lines."""
    previous_source: int | None = None
    previous_target: int | None = None
    mapped_count = 0
    has_adjacent_run = False
    for record_index in range(group_start, group_stop):
        source_line = positioned_lines[record_index][1]
        target_line = mapping.get_target_line_from_source_line(source_line)
        if (
            target_line is not None
            and previous_source is not None
            and previous_target is not None
            and source_line == previous_source + 1
            and target_line == previous_target + 1
        ):
            has_adjacent_run = True
        if target_line is not None:
            mapped_count += 1
        previous_source = source_line
        previous_target = target_line
    return (
        has_adjacent_run
        and mapped_count >= 2
        and mapped_count * 2 >= group_stop - group_start
    )


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
    presence_references: EffectivePresenceReferenceIndex,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
    *,
    prefer_source_mapping: bool,
    relocate_live_boundaries: bool,
) -> tuple[
    MappedRecordVector,
    MappedRecordVector,
    bool,
    LinePayloadOccurrenceIndex | None,
] | None:
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
    occurrence_index: LinePayloadOccurrenceIndex | None = None
    cached_reference = None
    cached_unique_position: int | None = None
    has_cached_reference = False

    for claimed_line in _presence_lines_without_replacements(
        presence_lines,
        replacement_source_ranges,
    ):
        if claimed_line > source_line_count:
            return None
        reference = presence_references.reference_for(claimed_line)
        position = None
        if not prefer_source_mapping:
            position = _find_baseline_insertion_position(
                reference,
                working_lines,
            )
            if position is None and relocate_live_boundaries:
                if has_cached_reference and reference == cached_reference:
                    position = cached_unique_position
                else:
                    needs_occurrence_index = (
                        reference is not None
                        and reference.has_after_line
                        and reference.after_line is not None
                        and reference.has_before_line
                        and reference.before_line is not None
                    )
                    if needs_occurrence_index and occurrence_index is None:
                        occurrence_index = LinePayloadOccurrenceIndex(
                            workspace,
                            working_lines,
                        )
                    position = unique_live_insertion_boundary_position(
                        reference,
                        working_lines,
                        occurrence_index,
                    )
                    cached_reference = reference
                    cached_unique_position = position
                    has_cached_reference = True
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

    return (
        positioned_lines,
        unmapped_lines,
        positioned_lines_are_ordered,
        occurrence_index,
    )


def _mapped_predecessor_identifies_saved_boundary(
    presence_references: EffectivePresenceReferenceIndex,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    claimed_line: int,
    mapped_source_line: int | None,
    mapped_target_line: int | None,
) -> bool:
    """Return whether one mapped source predecessor proves a saved boundary."""
    if (
        mapped_source_line is None
        or mapped_target_line is None
        or mapped_source_line >= claimed_line
        or mapped_source_line > len(source_lines)
    ):
        return False

    reference = presence_references.reference_for(claimed_line)
    if (
        reference is None
        or not reference.has_after_line
        or reference.after_line is None
        or reference.after_content is None
        or not reference.has_before_line
        or reference.before_line is None
    ):
        return False
    if normalized_line_payload(source_lines[mapped_source_line - 1]) != (
        normalized_line_payload(reference.after_content)
    ):
        return False
    return _insertion_boundary_identity_matches_at(
        reference,
        working_lines,
        mapped_target_line,
    )


def _unique_mapped_gap_insertion_position(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    mapping: LineMapping,
    occurrence_index: LinePayloadOccurrenceIndex,
    claimed_start: int,
    claimed_end: int,
    mapped_predecessor_source: int | None,
    mapped_predecessor_target: int | None,
) -> int | None:
    """Return a missing run's uniquely anchored, collapsed target gap."""
    following_source = claimed_end + 1
    if (
        mapped_predecessor_source != claimed_start - 1
        or mapped_predecessor_target is None
        or following_source > len(source_lines)
    ):
        return None

    following_target = mapping.get_target_line_from_source_line(
        following_source
    )
    if following_target != mapped_predecessor_target + 1:
        return None

    predecessor_content = source_lines[mapped_predecessor_source - 1]
    following_content = source_lines[following_source - 1]
    if (
        normalized_line_payload(
            working_lines[mapped_predecessor_target - 1]
        )
        != normalized_line_payload(predecessor_content)
        or normalized_line_payload(working_lines[following_target - 1])
        != normalized_line_payload(following_content)
    ):
        return None
    if (
        occurrence_index.occurrence_count(predecessor_content) != 1
        and occurrence_index.occurrence_count(following_content) != 1
    ):
        return None
    return mapped_predecessor_target


def _mapping_preserves_unpositioned_presence(
    plan: BaselineEditPlan,
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_references: EffectivePresenceReferenceIndex,
    unmapped_lines: MappedRecordVector,
    *,
    allow_adjacent_insertion: bool,
    allow_saved_mapped_boundary: bool,
    allow_unique_mapped_gap: bool,
    source_to_working_mapping: LineMapping | None,
    occurrence_index: LinePayloadOccurrenceIndex | None,
) -> bool:
    """Resolve unpositioned lines through mapping and adjacent mapped anchors."""
    if not unmapped_lines:
        return True

    if not plan.sort_target_spans_and_validate():
        return False

    target_lines_are_ordered = True
    previous_target_line: int | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        return False
    can_insert_adjacent = allow_adjacent_insertion
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
    current_saved_boundary: BaselineReference | None = None
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
            continues_missing_run = (
                previous_missing_source is not None
                and claimed_line == previous_missing_source + 1
            )
            if continues_missing_run and current_saved_boundary is not None:
                continues_missing_run = (
                    presence_references.reference_for(claimed_line)
                    == current_saved_boundary
                )
            if not continues_missing_run:
                run_end = claimed_line
                run_stop = record_index + 1
                reference = presence_references.reference_for(
                    claimed_line
                )
                while run_stop < len(unmapped_lines):
                    next_claimed_line = unmapped_lines[run_stop][0]
                    if (
                        next_claimed_line != run_end + 1
                        or presence_references.reference_for(next_claimed_line)
                        != reference
                        or mapping.get_target_line_from_source_line(
                            next_claimed_line
                        ) is not None
                    ):
                        break
                    run_end = next_claimed_line
                    run_stop += 1
                is_allowed_adjacent_insertion = (
                    can_insert_adjacent
                    and latest_mapped_source == claimed_line - 1
                    and latest_mapped_target is not None
                )
                has_saved_mapped_boundary = (
                    allow_saved_mapped_boundary
                    and _mapped_predecessor_identifies_saved_boundary(
                        presence_references,
                        source_lines,
                        working_lines,
                        claimed_line,
                        latest_mapped_source,
                        latest_mapped_target,
                    )
                )
                unique_mapped_gap_position = None
                if allow_unique_mapped_gap and not (
                    is_allowed_adjacent_insertion
                    or has_saved_mapped_boundary
                ):
                    if occurrence_index is None:
                        occurrence_index = LinePayloadOccurrenceIndex(
                            workspace,
                            working_lines,
                        )
                    unique_mapped_gap_position = (
                        _unique_mapped_gap_insertion_position(
                            source_lines,
                            working_lines,
                            mapping,
                            occurrence_index,
                            claimed_line,
                            run_end,
                            latest_mapped_source,
                            latest_mapped_target,
                        )
                    )
                if not (
                    is_allowed_adjacent_insertion
                    or has_saved_mapped_boundary
                    or unique_mapped_gap_position is not None
                ):
                    return False
                current_insertion_position = (
                    unique_mapped_gap_position
                    if unique_mapped_gap_position is not None
                    else latest_mapped_target
                )
                current_saved_boundary = (
                    None
                    if is_allowed_adjacent_insertion
                    else reference
                )
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
        current_saved_boundary = None
        source_scan = claimed_line + 1

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
    source_to_working_mapping: LineMapping | None,
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

        # A recorded boundary can restore a wholly missing insertion, but it
        # must not duplicate a presence run that structural matching already
        # found in part.  Let the structural strategy retain those mapped
        # lines and place only the missing source range.
        if (
            not group_matches
            and source_to_working_mapping is not None
            and _positioned_group_has_mapped_run(
                source_to_working_mapping,
                positioned_lines,
                group_start,
                group_stop,
            )
        ):
            return False

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
    presence_references: EffectivePresenceReferenceIndex,
    presence_lines: LineRanges,
    replacement_source_ranges: Sequence[tuple[int, ...]],
    *,
    allow_adjacent_unmapped_presence: bool,
    prefer_source_mapping: bool,
    trust_baseline_coordinates: bool,
    source_to_working_mapping: LineMapping | None,
    spool_dir: str | Path | None,
) -> tuple[MappedRecordVector, LineMapping | None] | None:
    """Plan explicit insertions and validate presence resolved by matching."""
    position_records = _collect_presence_position_records(
        workspace,
        len(source_lines),
        working_lines,
        presence_references,
        presence_lines,
        replacement_source_ranges,
        prefer_source_mapping=prefer_source_mapping,
        relocate_live_boundaries=not trust_baseline_coordinates,
    )
    if position_records is None:
        return None
    (
        positioned_lines,
        unmapped_lines,
        positioned_lines_are_ordered,
        occurrence_index,
    ) = position_records

    owned_mapping = None
    mapping = source_to_working_mapping
    if unmapped_lines and mapping is None:
        owned_mapping = _match_lines(
            source_lines,
            working_lines,
            spool_dir=spool_dir,
        )
        mapping = owned_mapping

    transfer_owned_mapping = False
    try:
        if not _mapping_preserves_unpositioned_presence(
            plan,
            workspace,
            source_lines,
            working_lines,
            presence_references,
            unmapped_lines,
            allow_adjacent_insertion=allow_adjacent_unmapped_presence,
            allow_saved_mapped_boundary=not trust_baseline_coordinates,
            allow_unique_mapped_gap=not trust_baseline_coordinates,
            source_to_working_mapping=mapping,
            occurrence_index=occurrence_index,
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
            source_to_working_mapping=mapping,
        ):
            return None

        transfer_owned_mapping = True
        return positioned_lines, owned_mapping
    finally:
        if owned_mapping is not None and not transfer_owned_mapping:
            owned_mapping.close()
