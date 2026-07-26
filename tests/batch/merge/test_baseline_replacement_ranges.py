"""Tests for storage-backed baseline replacement source ranges."""

from git_stage_batch.batch.merge.baseline_replacement_ranges import (
    replacement_source_range_capacity,
)


def test_replacement_source_range_capacity_counts_string_records() -> None:
    """Mapped range allocation should cover every string record."""
    assert replacement_source_range_capacity(["7-9,1-3", "6", "2-4"]) == 4
