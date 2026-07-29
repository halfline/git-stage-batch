"""Tests for coordinate-versus-structural merge strategy decisions."""

from git_stage_batch.batch.merge.coordinate_strategy import (
    has_recorded_baseline_coordinates,
)
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.core.line_selection import LineRanges


class _IterationGuardedSelection(LineRanges):
    """Selection whose individual lines must not be expanded."""

    __slots__ = ()

    def __iter__(self):
        raise AssertionError("selected line ranges must not be expanded")


def test_coordinate_detection_scans_references_not_selected_lines() -> None:
    """Coordinate detection must scale with edits, not selected line count."""
    line_count = 1_000_000
    reference = BaselineReference(
        after_line=None,
        after_content=None,
        has_after_line=True,
        before_line=None,
        before_content=None,
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{line_count}"],
        baseline_references={line_count: reference},
    )

    assert has_recorded_baseline_coordinates(
        ownership,
        _IterationGuardedSelection.from_ranges(((line_count, line_count),)),
        [],
    )
