"""Tests for live-hunk replacement-run translation."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership import (
    hunk_replacement_translation as hunk_replacement_translation_module,
)
from git_stage_batch.batch.ownership.claims import LineRangeBuilder
from git_stage_batch.batch.ownership.hunk_replacement_translation import (
    translate_hunk_replacement_line_runs,
)
from git_stage_batch.batch.ownership.line_entries import (
    LineEntryContentSequence,
)
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.replacement_line_runs import ReplacementLineRun
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import LineEntry


def old_line_content_by_number(
    hunk_lines: list[LineEntry],
) -> dict[int, bytes]:
    """Return the small fixture's visible old-line content by coordinate."""
    return {
        line.old_line_number: line.text_bytes
        for line in hunk_lines
        if line.old_line_number is not None and line.kind in {" ", "-"}
    }


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


def test_translate_hunk_replacement_line_runs_closes_inputs_on_error():
    """Translation failures must release both streamed comparison inputs."""

    class ClosableRuns:
        def __init__(self, runs):
            self._runs = iter(runs)
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._runs)

        def close(self):
            self.closed = True

    displayed_runs = ClosableRuns((ReplacementLineRun(1, 1, 1, 1),))
    origin_runs = ClosableRuns((ReplacementLineRun(1, 1, 1, 1),))
    lines = [
        LineEntry(
            id=1,
            kind="-",
            old_line_number=1,
            new_line_number=None,
            text_bytes=b"old",
            source_line=None,
        ),
        LineEntry(
            id=2,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"new",
            source_line=None,
        ),
    ]

    with pytest.raises(ValueError, match="source_line is None"):
        translate_hunk_replacement_line_runs(
            hunk_lines=lines,
            selected_display_ids={1, 2},
            replacement_line_runs=displayed_runs,
            old_line_content=old_line_content_by_number(lines),
            hunk_content_view=LineEntryContentSequence(lines),
            replacement_origin_line_runs=origin_runs,
            replacement_origin_source_lines=[b"old\n"],
        )

    assert displayed_runs.closed is True
    assert origin_runs.closed is True

    same_stream_runs = ClosableRuns((ReplacementLineRun(1, 1, 1, 1),))
    with pytest.raises(ValueError, match="source_line is None"):
        translate_hunk_replacement_line_runs(
            hunk_lines=lines,
            selected_display_ids={1, 2},
            replacement_line_runs=same_stream_runs,
            old_line_content=old_line_content_by_number(lines),
            hunk_content_view=LineEntryContentSequence(lines),
            replacement_origin_source_lines=[b"old\n"],
            replacement_runs_are_origin_runs=True,
        )

    assert same_stream_runs.closed is True


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


def test_translate_hunk_replacement_uses_origin_baseline_content():
    """Displayed index bytes must not become persistent replacement removals."""
    lines = [
        LineEntry(
            id=1,
            kind="-",
            old_line_number=3,
            new_line_number=None,
            text_bytes=b"staged-one",
            source_line=1,
        ),
        LineEntry(
            id=2,
            kind="-",
            old_line_number=4,
            new_line_number=None,
            text_bytes=b"staged-two",
            source_line=1,
        ),
        LineEntry(
            id=3,
            kind="+",
            old_line_number=None,
            new_line_number=3,
            text_bytes=b"work-one",
            source_line=3,
        ),
        LineEntry(
            id=4,
            kind="+",
            old_line_number=None,
            new_line_number=4,
            text_bytes=b"work-two",
            source_line=4,
        ),
    ]
    displayed_run = ReplacementLineRun(
        old_start=3,
        old_end=4,
        new_start=3,
        new_end=4,
    )
    origin_run = ReplacementLineRun(
        old_start=2,
        old_end=3,
        new_start=3,
        new_end=4,
    )
    head_lines = [b"head\n", b"orig-one\n", b"orig-two\n", b"tail\n"]

    result = translate_hunk_replacement_line_runs(
        hunk_lines=lines,
        selected_display_ids={1, 3},
        replacement_line_runs=(displayed_run,),
        old_line_content=old_line_content_by_number(lines),
        hunk_content_view=LineEntryContentSequence(lines),
        replacement_origin_line_runs=(origin_run,),
        replacement_origin_source_lines=head_lines,
    )

    assert list(result.absence_claims[0].content_lines) == [b"orig-one\n"]
    reference = result.absence_claims[0].baseline_reference
    assert reference is not None
    assert reference.after_line == 1
    assert reference.before_line == 3
    assert reference.before_content == b"orig-two\n"


def test_same_stream_replacement_origin_matches_independent_origin_stream():
    """One replacement stream should retain the two-stream origin metadata."""
    lines = [
        LineEntry(
            id=1,
            kind="-",
            old_line_number=1,
            new_line_number=None,
            text_bytes=b"displayed-one",
            source_line=None,
        ),
        LineEntry(
            id=2,
            kind="-",
            old_line_number=2,
            new_line_number=None,
            text_bytes=b"displayed-two",
            source_line=1,
        ),
        LineEntry(
            id=3,
            kind="+",
            old_line_number=None,
            new_line_number=1,
            text_bytes=b"new-one",
            source_line=1,
        ),
        LineEntry(
            id=4,
            kind="+",
            old_line_number=None,
            new_line_number=2,
            text_bytes=b"new-two",
            source_line=2,
        ),
    ]
    run = ReplacementLineRun(1, 2, 1, 2)
    origin_lines = [b"origin-one\n", b"origin-two\n", b"tail\n"]

    same_stream = translate_hunk_replacement_line_runs(
        hunk_lines=lines,
        selected_display_ids=LineRanges.from_ranges(((1, 1), (3, 3))),
        replacement_line_runs=(run,),
        old_line_content=old_line_content_by_number(lines),
        hunk_content_view=LineEntryContentSequence(lines),
        replacement_origin_source_lines=origin_lines,
        replacement_runs_are_origin_runs=True,
    )
    independent_streams = translate_hunk_replacement_line_runs(
        hunk_lines=lines,
        selected_display_ids={1, 3},
        replacement_line_runs=(run,),
        old_line_content=old_line_content_by_number(lines),
        hunk_content_view=LineEntryContentSequence(lines),
        replacement_origin_line_runs=(run,),
        replacement_origin_source_lines=origin_lines,
    )

    def metadata_signature(result):
        return (
            result.claimed_source_lines.ranges(),
            result.presence_baseline_references,
            tuple(
                (
                    claim.anchor_line,
                    tuple(claim.content_lines),
                    claim.baseline_reference,
                )
                for claim in result.absence_claims
            ),
            result.replacement_units,
            result.consumed_display_ids.ranges(),
        )

    assert metadata_signature(same_stream) == metadata_signature(
        independent_streams
    )
    assert tuple(same_stream.absence_claims[0].content_lines) == (
        b"origin-one\n",
    )


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
    selected_ids = LineRanges.from_ranges(((1, len(lines)),))

    result = _translate(lines, selected_ids, [RangeOnlyReplacementRun()])

    assert result.claimed_source_lines.ranges() == ((1, 1000),)
    assert result.replacement_units == [
        ReplacementUnit(presence_lines=["1-1000"], deletion_indices=[0]),
    ]
    assert result.consumed_display_ids == selected_ids


def test_equal_replacement_consumed_ids_stay_compact(monkeypatch):
    """Paired old/new IDs should accumulate in separate monotonic ranges."""
    builders = []

    class TrackingLineRangeBuilder(LineRangeBuilder):
        def __init__(self):
            super().__init__()
            builders.append(self)

    monkeypatch.setattr(
        hunk_replacement_translation_module,
        "LineRangeBuilder",
        TrackingLineRangeBuilder,
    )
    line_count = 64
    lines = [
        *[
            LineEntry(
                id=index + 1,
                kind="-",
                old_line_number=index + 1,
                new_line_number=None,
                text_bytes=b"old",
            )
            for index in range(line_count)
        ],
        *[
            LineEntry(
                id=line_count + index + 1,
                kind="+",
                old_line_number=None,
                new_line_number=index + 1,
                text_bytes=b"new",
                source_line=index + 1,
            )
            for index in range(line_count)
        ],
    ]
    selected_ids = LineRanges.from_ranges(((1, line_count * 2),))

    result = _translate(
        lines,
        selected_ids,
        (ReplacementLineRun(1, line_count, 1, line_count),),
    )

    assert result.consumed_display_ids == selected_ids
    assert max(len(builder.ranges) for builder in builders) <= 1
