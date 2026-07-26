"""Storage-backed source ranges for baseline replacement units."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.line_selection import scan_line_range_specs
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
