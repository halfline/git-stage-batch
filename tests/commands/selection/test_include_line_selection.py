"""Focused tests for live include-line selection metadata."""

from git_stage_batch.commands.selection.include_line_selection import (
    record_baseline_references_for_additions,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange


def _line_changes(lines: list[LineEntry]) -> LineLevelChange:
    return LineLevelChange(
        path="module.py",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=len(lines)),
        lines=lines,
    )


def test_diff_references_record_explicit_eof_boundary() -> None:
    """An EOF addition should distinguish EOF from an unknown next boundary."""
    base = LineEntry(
        id=None,
        kind=" ",
        old_line_number=1,
        new_line_number=1,
        text_bytes=b"base",
        source_line=1,
    )
    addition = LineEntry(
        id=1,
        kind="+",
        old_line_number=None,
        new_line_number=2,
        text_bytes=b"added",
        source_line=2,
    )

    record_baseline_references_for_additions(_line_changes([base, addition]))

    assert addition.has_baseline_reference_after
    assert addition.baseline_reference_after_line == 1
    assert addition.baseline_reference_after_text_bytes == b"base"
    assert addition.has_baseline_reference_before
    assert addition.baseline_reference_before_line is None
    assert addition.baseline_reference_before_text_bytes is None
