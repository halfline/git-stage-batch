"""Build realized entries from matched working-tree ranges."""

from __future__ import annotations

from collections.abc import Sequence

from .line_mapping import LineMapping
from .realized_entries import _RealizedEntries, _backing_content_sequence
from ..core.line_selection import LineSelection


def _source_lines_are_contiguous(
    previous_source_line: int | None,
    source_line: int | None,
) -> bool:
    if previous_source_line is None or source_line is None:
        return previous_source_line is None and source_line is None
    return source_line == previous_source_line + 1


def append_working_range_with_mapping(
    result: _RealizedEntries,
    working_lines: Sequence[bytes],
    mapping: LineMapping,
    start: int,
    end: int,
    presence_line_set: LineSelection,
) -> None:
    """Append a target range split only where provenance stops being contiguous."""
    if start == end:
        return

    content_lines = _backing_content_sequence(working_lines)
    run_start = start
    run_source_start = mapping.get_source_line_from_target_line(start + 1)
    previous_source_line = run_source_start
    run_is_claimed = (
        run_source_start in presence_line_set
        if run_source_start is not None
        else False
    )

    for working_idx in range(start + 1, end):
        source_line = mapping.get_source_line_from_target_line(working_idx + 1)
        is_claimed = (
            source_line in presence_line_set
            if source_line is not None
            else False
        )
        if (
            is_claimed == run_is_claimed
            and _source_lines_are_contiguous(
                previous_source_line,
                source_line,
            )
        ):
            previous_source_line = source_line
            continue

        result.append_line_range_from(
            content_lines,
            run_start,
            working_idx,
            source_line_start=run_source_start,
            target_line_start=run_start + 1,
            is_claimed=run_is_claimed,
        )
        run_start = working_idx
        run_source_start = source_line
        previous_source_line = source_line
        run_is_claimed = is_claimed

    result.append_line_range_from(
        content_lines,
        run_start,
        end,
        source_line_start=run_source_start,
        target_line_start=run_start + 1,
        is_claimed=run_is_claimed,
    )
