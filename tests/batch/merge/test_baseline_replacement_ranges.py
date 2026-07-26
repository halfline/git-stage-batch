"""Tests for storage-backed baseline replacement source ranges."""

from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.merge.baseline_replacement_ranges import (
    collect_replacement_source_ranges,
    replacement_source_range_capacity,
    selected_replacement_source_ranges,
)
from git_stage_batch.core.line_selection import LineRanges


def test_replacement_source_range_capacity_counts_string_records() -> None:
    """Mapped range allocation should cover every string record."""
    assert replacement_source_range_capacity(["7-9,1-3", "6", "2-4"]) == 4


def test_replacement_source_ranges_normalize_in_mapped_storage() -> None:
    """Unordered and overlapping metadata should retain LineRanges semantics."""
    with MatcherWorkspace() as workspace:
        source_ranges = collect_replacement_source_ranges(
            workspace,
            ["7-9,1-3", "6", "2-4"],
        )

        assert source_ranges is not None
        assert tuple(source_ranges) == ((1, 4), (6, 9))


def test_selected_replacement_source_ranges_stream_intersections() -> None:
    """Selected source intersections should stay range-backed."""
    class FragmentedSourceRanges:
        def __init__(self, count: int) -> None:
            self.count = count
            self.maximum_read_index = 0

        def __len__(self) -> int:
            return self.count

        def __getitem__(self, index: int) -> tuple[int, int]:
            if index < 0 or index >= self.count:
                raise IndexError(index)
            if index > self.maximum_read_index:
                raise AssertionError("intersections were collected before yielding")
            source_line = 2 * index + 1
            return source_line, source_line

    source_ranges = FragmentedSourceRanges(1000)
    selected_lines = LineRanges.from_ranges(((1, 1999),))
    intersections = selected_replacement_source_ranges(
        source_ranges,
        selected_lines,
    )

    assert next(intersections) == (1, 1)
    source_ranges.maximum_read_index = len(source_ranges) - 1
    last_intersection = None
    remaining_count = 0
    for last_intersection in intersections:
        remaining_count += 1

    assert remaining_count == 999
    assert last_intersection == (1999, 1999)
