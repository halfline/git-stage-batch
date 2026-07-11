"""Tests for LineEntry ownership helper functions."""

from __future__ import annotations

from git_stage_batch.batch.ownership.line_entries import (
    baseline_reference_for_presence_line,
)
from git_stage_batch.core.models import LineEntry


def test_baseline_reference_for_presence_line_returns_none_without_metadata():
    """Presence lines without baseline markers should not get references."""
    line = LineEntry(
        id=1,
        kind="+",
        old_line_number=None,
        new_line_number=1,
        text_bytes=b"line",
        source_line=3,
    )

    assert baseline_reference_for_presence_line(line) is None


def test_baseline_reference_for_presence_line_reads_entry_metadata():
    """Presence-line baseline markers should become ownership references."""
    line = LineEntry(
        id=1,
        kind="+",
        old_line_number=None,
        new_line_number=1,
        text_bytes=b"line",
        source_line=3,
        baseline_reference_after_line=2,
        baseline_reference_after_text_bytes=b"before",
        has_baseline_reference_after=True,
        baseline_reference_before_line=4,
        baseline_reference_before_text_bytes=b"after",
        has_baseline_reference_before=True,
    )

    reference = baseline_reference_for_presence_line(line)

    assert reference is not None
    assert reference.after_line == 2
    assert reference.after_content == b"before"
    assert reference.has_after_line
    assert reference.before_line == 4
    assert reference.before_content == b"after"
    assert reference.has_before_line
