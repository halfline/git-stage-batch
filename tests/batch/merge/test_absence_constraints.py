"""Tests for absence-constraint candidate metadata."""

from git_stage_batch.batch.merge.absence_constraints import (
    absence_choices_for_claim,
)
from git_stage_batch.batch.realization.entries import RealizedEntry


def test_absence_choice_reports_line_after_complete_removed_sequence():
    """Candidate bounds should surround the whole multi-line removal."""
    entries = [
        RealizedEntry(b"head\n", 1),
        RealizedEntry(b"old-a\n", None),
        RealizedEntry(b"old-b\n", None),
        RealizedEntry(b"tail\n", 2),
    ]

    choices = absence_choices_for_claim(
        entries,
        1,
        [b"old-a\n", b"old-b\n"],
    )

    assert choices[0].target_after_line == 1
    assert choices[0].target_before_line == 4
