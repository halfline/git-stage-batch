"""Tests for live-hunk replacement-run translation."""

from __future__ import annotations

from git_stage_batch.batch.ownership.hunk_replacement_translation import (
    translate_hunk_replacement_line_runs,
)
from git_stage_batch.batch.ownership.line_entries import (
    LineEntryContentSequence,
    old_line_content_by_number,
)
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.replacement_line_runs import ReplacementLineRun
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import LineEntry


def _translate(lines, selected_ids, replacement_runs):
    return translate_hunk_replacement_line_runs(
        hunk_lines=lines,
        selected_display_ids=selected_ids,
        replacement_line_runs=replacement_runs,
        old_line_content=old_line_content_by_number(lines),
        hunk_content_view=LineEntryContentSequence(lines),
    )


def test_translate_hunk_replacement_line_runs_returns_empty_result():
    """Missing replacement runs should produce empty ownership fragments."""
    result = _translate([], set(), [])

    assert not result.claimed_source_lines
    assert result.presence_baseline_references == {}
    assert result.absence_claims == []
    assert result.replacement_units == []
    assert result.consumed_display_ids == set()


def test_translate_hunk_replacement_line_runs_builds_replacement_result():
    """Selected replacement pairs should produce ownership fragments."""
    lines = [
        LineEntry(
            id=1,
            kind="-",
            old_line_number=1,
            new_line_number=None,
            text_bytes=b"a",
            source_line=None,
        ),
        LineEntry(
            id=2,
            kind="-",
            old_line_number=2,
            new_line_number=None,
            text_bytes=b"b",
            source_line=1,
        ),
        LineEntry(
            id=3,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"A",
            source_line=1,
            baseline_reference_after_line=2,
            baseline_reference_after_text_bytes=b"b",
            has_baseline_reference_after=True,
        ),
        LineEntry(
            id=4,
            kind="+",
            old_line_number=None,
            new_line_number=2,
            text_bytes=b"B",
            source_line=2,
        ),
    ]

    result = _translate(
        lines,
        {1, 3},
        [
            ReplacementLineRun(
                old_start=1,
                old_end=2,
                new_start=1,
                new_end=2,
            ),
        ],
    )

    assert result.claimed_source_lines == {1}
    assert list(result.absence_claims[0].content_lines) == [b"a\n"]
    assert result.presence_baseline_references[1].after_line == 2
    assert result.presence_baseline_references[1].after_content == b"b"
    assert result.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]
    assert result.replacement_units[0].origin.old_start == 1
    assert result.replacement_units[0].origin.old_end == 2
    assert result.replacement_units[0].origin.new_start == 1
    assert result.replacement_units[0].origin.new_end == 2
    assert result.consumed_display_ids == {1, 3}


def test_translate_hunk_replacement_line_runs_keeps_large_ranges_compact(
    monkeypatch,
):
    """Large replacement selections should not build line sets."""

    def fail_from_lines(cls, lines):
        raise AssertionError("replacement translation should preserve ranges")

    class RangeOnlyReplacementRun:
        old_start = 1
        old_end = 1
        new_start = 1
        new_end = 1000

    monkeypatch.setattr(LineRanges, "from_lines", classmethod(fail_from_lines))

    lines = [
        LineEntry(
            id=1,
            kind="-",
            old_line_number=1,
            new_line_number=None,
            text_bytes=b"old",
            source_line=None,
        ),
        *[
            LineEntry(
                id=index + 2,
                kind="+",
                old_line_number=None,
                new_line_number=index + 1,
                text_bytes=f"new {index}".encode(),
                source_line=index + 1,
            )
            for index in range(1000)
        ],
    ]
    selected_ids = {line.id for line in lines if line.id is not None}

    result = _translate(lines, selected_ids, [RangeOnlyReplacementRun()])

    assert result.claimed_source_lines.ranges() == ((1, 1000),)
    assert result.replacement_units == [
        ReplacementUnit(presence_lines=["1-1000"], deletion_indices=[0]),
    ]
    assert result.consumed_display_ids == selected_ids
