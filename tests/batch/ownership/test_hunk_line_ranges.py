"""Tests for live-hunk line range scanning helpers."""

from __future__ import annotations

from git_stage_batch.batch.ownership.hunk_line_ranges import (
    HunkLineRangeScan,
    hunk_line_index_ranges_in_range,
    hunk_line_indexes_in_range,
    scan_hunk_line_range,
)
from git_stage_batch.core.models import LineEntry


def _entry(
    line_id: int | None,
    kind: str,
    *,
    old_line: int | None = None,
    new_line: int | None = None,
) -> LineEntry:
    return LineEntry(
        id=line_id,
        kind=kind,
        old_line_number=old_line,
        new_line_number=new_line,
        text_bytes=b"line",
    )


def test_scan_hunk_line_range_counts_selected_lines():
    """Scans should count matching hunk lines inside the requested range."""
    lines = [
        _entry(None, " ", old_line=1, new_line=1),
        _entry(1, "-", old_line=2),
        _entry(2, "+", new_line=2),
        _entry(3, "-", old_line=3),
        _entry(4, "-", old_line=4),
        _entry(None, " ", old_line=5, new_line=3),
    ]

    scan = scan_hunk_line_range(
        lines,
        0,
        kind="-",
        line_number_attr="old_line_number",
        start=2,
        end=4,
        selected_display_ids={1, 4},
    )

    assert scan == HunkLineRangeScan(
        start=2,
        end=4,
        start_index=1,
        stop_index=5,
        count=3,
        selected_count=2,
    )
    assert scan.complete
    assert not scan.fully_selected
    assert list(
        hunk_line_indexes_in_range(
            lines,
            scan,
            kind="-",
            line_number_attr="old_line_number",
        )
    ) == [1, 3, 4]
    assert list(
        hunk_line_index_ranges_in_range(
            lines,
            scan,
            kind="-",
            line_number_attr="old_line_number",
        )
    ) == [(1, 2), (3, 5)]


def test_scan_hunk_line_range_respects_cursor():
    """Scans should start at the supplied cursor for replacement runs."""
    lines = [
        _entry(1, "+", new_line=1),
        _entry(None, " ", old_line=1, new_line=2),
        _entry(3, "+", new_line=3),
    ]

    scan = scan_hunk_line_range(
        lines,
        1,
        kind="+",
        line_number_attr="new_line_number",
        start=1,
        end=3,
        selected_display_ids={3},
    )

    assert scan == HunkLineRangeScan(
        start=1,
        end=3,
        start_index=2,
        stop_index=3,
        count=1,
        selected_count=1,
    )
    assert not scan.complete
    assert not scan.fully_selected
