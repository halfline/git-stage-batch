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


def test_snapshot_references_preserve_start_of_file_insertion() -> None:
    """A two-sided start reference should retain the first baseline gap."""
    addition = LineEntry(
        id=1,
        kind="+",
        old_line_number=None,
        new_line_number=1,
        text_bytes=b"added",
        source_line=1,
    )
    tail = LineEntry(
        id=None,
        kind=" ",
        old_line_number=1,
        new_line_number=2,
        text_bytes=b"tail",
        source_line=2,
    )

    record_baseline_references_for_additions(
        _line_changes([addition, tail]),
        baseline_lines=[b"tail\n"],
        source_lines=[b"added\n", b"tail\n"],
    )

    assert addition.has_baseline_reference_after
    assert addition.baseline_reference_after_line is None
    assert addition.baseline_reference_after_text_bytes is None
    assert addition.has_baseline_reference_before
    assert addition.baseline_reference_before_line == 1
    assert addition.baseline_reference_before_text_bytes == b"tail"


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


def test_snapshot_references_reanchor_stale_eof_additions() -> None:
    """A grown index should move additions past an earlier staged EOF line."""
    base = LineEntry(
        id=None,
        kind=" ",
        old_line_number=1,
        new_line_number=1,
        text_bytes=b"base",
        source_line=1,
    )
    first = LineEntry(
        id=1,
        kind="+",
        old_line_number=None,
        new_line_number=2,
        text_bytes=b"first",
        source_line=2,
    )
    blank = LineEntry(
        id=2,
        kind="+",
        old_line_number=None,
        new_line_number=3,
        text_bytes=b"",
        source_line=3,
    )

    record_baseline_references_for_additions(
        _line_changes([base, first, blank]),
        baseline_lines=[b"base\n", b"first\n"],
        source_lines=[b"base\n", b"first\n", b"\n"],
    )

    assert first.baseline_reference_after_line == 1
    assert first.baseline_reference_before_line == 2
    assert first.baseline_reference_before_text_bytes == b"first\n"
    assert blank.has_baseline_reference_after
    assert blank.baseline_reference_after_line == 2
    assert blank.baseline_reference_after_text_bytes == b"first\n"
    assert blank.has_baseline_reference_before
    assert blank.baseline_reference_before_line is None
    assert blank.baseline_reference_before_text_bytes is None


def test_snapshot_references_clear_ambiguous_stale_reference() -> None:
    """Target-only content should leave no already-invalid insertion reference."""
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
        text_bytes=b"selected",
        source_line=2,
    )
    tail = LineEntry(
        id=None,
        kind=" ",
        old_line_number=2,
        new_line_number=3,
        text_bytes=b"tail",
        source_line=3,
    )

    record_baseline_references_for_additions(
        _line_changes([base, addition, tail]),
        baseline_lines=[b"base\n", b"staged only\n", b"tail\n"],
        source_lines=[b"base\n", b"selected\n", b"tail\n"],
    )

    assert not addition.has_baseline_reference_after
    assert addition.baseline_reference_after_line is None
    assert addition.baseline_reference_after_text_bytes is None
    assert not addition.has_baseline_reference_before
    assert addition.baseline_reference_before_line is None
    assert addition.baseline_reference_before_text_bytes is None
