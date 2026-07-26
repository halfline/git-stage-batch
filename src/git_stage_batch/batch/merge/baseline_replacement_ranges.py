"""Storage-backed source ranges for baseline replacement units."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ...core.line_selection import LineRanges, scan_line_range_specs
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ..line_matching.match_workspace import MatcherWorkspace


def collect_replacement_source_ranges(
    workspace: MatcherWorkspace,
    range_specs: Sequence[str],
) -> MappedRecordVector | None:
    """Parse and normalize one replacement selection in mapped storage."""
    source_ranges = workspace.record_vector(
        replacement_source_range_capacity(range_specs),
        "QQ",
    )
    try:
        for source_start, source_end in scan_line_range_specs(range_specs):
            if source_end > 0xFFFF_FFFF_FFFF_FFFF:
                workspace.close_resource(source_ranges)
                return None
            source_ranges.append((source_start, source_end))
    except (TypeError, ValueError, OverflowError):
        workspace.close_resource(source_ranges)
        return None

    if len(source_ranges) > 1:
        sort_mapped_records(source_ranges)
        _compact_source_ranges(source_ranges)
    return source_ranges


def replacement_source_range_capacity(
    range_specs: Sequence[str],
) -> int:
    """Return an upper bound for records parsed from range specifications."""
    return sum(range_spec.count(",") + 1 for range_spec in range_specs)


def selected_replacement_source_ranges(
    source_ranges: Sequence[tuple[int, ...]],
    selected_lines: LineRanges,
) -> Iterator[tuple[int, int]]:
    """Yield intersections between normalized source ranges and a selection."""
    selected_ranges = selected_lines.ranges()
    source_range_index = 0
    selected_range_index = 0
    while source_range_index < len(source_ranges) and selected_range_index < len(
        selected_ranges
    ):
        source_start, source_end = source_ranges[source_range_index]
        selected_start, selected_end = selected_ranges[selected_range_index]
        start = max(source_start, selected_start)
        end = min(source_end, selected_end)
        if start <= end:
            yield start, end

        if source_end < selected_end:
            source_range_index += 1
        else:
            selected_range_index += 1


def _compact_source_ranges(source_ranges: MappedRecordVector) -> None:
    retained_count = 0
    for source_start, source_end in source_ranges:
        if retained_count:
            previous_start, previous_end = source_ranges[retained_count - 1]
            if source_start <= previous_end + 1:
                source_ranges[retained_count - 1] = (
                    previous_start,
                    max(previous_end, source_end),
                )
                continue
        source_ranges[retained_count] = (source_start, source_end)
        retained_count += 1
    source_ranges.truncate(retained_count)
