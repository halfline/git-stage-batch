"""Tests for file-review row segmentation."""

from __future__ import annotations

from git_stage_batch.core.actionable_changes import (
    ActionableSelection,
    ActionableSelectionReason,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.output.file_review_change_segments import (
    build_file_review_change_segments,
)


def _line(
    line_id: int | None,
    kind: str,
    *,
    old_line: int | None = None,
    new_line: int | None = None,
    source_line: int | None = None,
) -> LineEntry:
    return LineEntry(
        id=line_id,
        kind=kind,
        old_line_number=old_line,
        new_line_number=new_line,
        source_line=source_line,
        text_bytes=b"line",
    )


def _line_changes(lines: list[LineEntry]) -> LineLevelChange:
    return LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=lines,
    )


def _actionable(selection_id: int) -> ActionableSelection:
    return ActionableSelection(
        display_ids=(selection_id,),
        selection_ids=(selection_id,),
        reason=ActionableSelectionReason.SIMPLE,
    )


def test_file_review_segments_start_new_segment_after_gap():
    """Gap rows should separate neighboring file-review changes."""
    first_action = _actionable(1)
    second_action = _actionable(2)
    gap = _line(None, " ")
    line_changes = _line_changes(
        [
            _line(None, " ", old_line=1, new_line=1),
            _line(1, "+", new_line=2),
            gap,
            _line(2, "+", new_line=5),
        ]
    )

    segments = build_file_review_change_segments(
        line_changes,
        (first_action, second_action),
        None,
    )

    assert len(segments) == 2
    assert [line.id for line in segments[0].rows] == [None, 1]
    assert segments[0].actionable is first_action
    assert segments[1].rows[0] is gap
    assert [line.id for line in segments[1].rows] == [None, 2]
    assert segments[1].actionable is second_action


def test_file_review_segments_split_by_displayability():
    """Hidden changed rows should not share an actionable segment."""
    action = _actionable(1)
    trailing = _line(None, " ", old_line=2, new_line=3)
    line_changes = _line_changes(
        [
            _line(1, "+", new_line=1),
            _line(2, "+", new_line=2),
            trailing,
        ]
    )

    segments = build_file_review_change_segments(
        line_changes,
        (action,),
        {1: 1},
    )

    assert len(segments) == 2
    assert [line.id for line in segments[0].rows] == [1]
    assert segments[0].actionable is action
    assert [line.id for line in segments[1].rows] == [2, None]
    assert segments[1].rows[-1] is trailing
    assert segments[1].actionable is None
