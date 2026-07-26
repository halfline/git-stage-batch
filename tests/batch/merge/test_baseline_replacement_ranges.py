"""Tests for storage-backed baseline replacement source ranges."""

from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.merge.baseline_replacement_ranges import (
    collect_replacement_source_ranges,
    replacement_source_range_capacity,
)


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
