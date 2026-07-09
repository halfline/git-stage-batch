"""Missing claimed source-line detection for presence constraints."""

from __future__ import annotations

from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from .line_mapping import LineMapping


def mapped_missing_source_lines(
    source_lines: LineSelection,
    source_line_count: int,
    mapping: LineMapping,
) -> LineRanges:
    """Return selected source lines that have no mapped target line."""
    missing_ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None
    source_selection = coerce_line_ranges(source_lines)

    for start, end in source_selection.ranges():
        for source_line in range(max(1, start), min(end, source_line_count) + 1):
            if mapping.get_target_line_from_source_line(source_line) is not None:
                if current_start is not None and current_end is not None:
                    missing_ranges.append((current_start, current_end))
                    current_start = None
                    current_end = None
                continue

            if current_start is None:
                current_start = source_line
            current_end = source_line

        if current_start is not None and current_end is not None:
            missing_ranges.append((current_start, current_end))
            current_start = None
            current_end = None

    if current_start is not None and current_end is not None:
        missing_ranges.append((current_start, current_end))

    return LineRanges.from_ranges(missing_ranges)
