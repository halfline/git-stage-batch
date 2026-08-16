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


def test_absence_choices_stop_streaming_at_requested_limit():
    """Repeated anchors must not become an unbounded Python position tuple."""

    class RepeatedAnchorEntries:
        read_count = 0

        def __len__(self):
            return 200_000

        def __getitem__(self, index):
            if index < 0 or index >= len(self):
                raise IndexError(index)
            self.read_count += 1
            if index % 2 == 0:
                return RealizedEntry(b"anchor\n", 1)
            return RealizedEntry(b"old\n", None)

    entries = RepeatedAnchorEntries()
    choices = absence_choices_for_claim(
        entries,  # type: ignore[arg-type]
        1,
        [b"old\n"],
        max_results=7,
    )

    assert len(choices) == 7
    assert entries.read_count < 100
