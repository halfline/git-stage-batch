"""Tests for selected-line batch command parsing."""

from unittest.mock import Mock

import pytest

from git_stage_batch.batch.ownership.resolved_presence_alternatives import (
    resolve_presence_source_alternatives,
)
from git_stage_batch.batch.ownership_update import SourceBoundLineSelection
from git_stage_batch.commands.selection.batch_line_selection import (
    build_worktree_discard_selection,
    select_lines_for_batch_action,
)
from git_stage_batch.core.coordinates import BatchSourceSpace, content_snapshot
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.exceptions import CommandError


def test_malformed_batch_line_selection_becomes_command_error():
    """Malformed batch selections should remain inside the command boundary."""
    line_changes = Mock(spec=LineLevelChange)

    with pytest.raises(CommandError, match="Invalid line ID: abc"):
        select_lines_for_batch_action(line_changes, "abc")


@pytest.mark.parametrize(
    ("trailing_blank_count", "separator_kind", "expected_separator_id"),
    [
        (0, "+", None),
        (2, "+", 1),
        (1, "+", None),
        (2, " ", None),
    ],
)
def test_worktree_discard_separates_structural_eof_cleanup_from_ownership(
    trailing_blank_count,
    separator_kind,
    expected_separator_id,
):
    """A redundant added separator affects only the worktree selection."""
    separator_id = 1 if separator_kind == "+" else None
    separator_old_line = None if separator_kind == "+" else 1
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(0, 0, 1, 2 + trailing_blank_count),
        lines=[
            LineEntry(
                separator_id,
                separator_kind,
                separator_old_line,
                1,
                text_bytes=b"",
            ),
            LineEntry(2, "+", None, 2, text_bytes=b"selected"),
            *[
                LineEntry(
                    3 + offset,
                    "+",
                    None,
                    3 + offset,
                    text_bytes=b"",
                )
                for offset in range(trailing_blank_count)
            ],
        ],
    )
    ownership_ids = {2}
    working_lines = [
        b"\n",
        b"selected\n",
        *([b"\n"] * trailing_blank_count),
    ]
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", working_lines, space=BatchSourceSpace),
        [line_changes.lines[1].with_source_line(2)],
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        ownership_ids,
        working_lines,
        source_selection,
    )

    assert discard_selection.requested_ids is ownership_ids
    assert discard_selection.owned_blank_id is None
    assert discard_selection.cleanup_blank_id == expected_separator_id
    assert set(discard_selection) == (
        ownership_ids
        if expected_separator_id is None
        else ownership_ids | {expected_separator_id}
    )


@pytest.mark.parametrize(
    ("selected_source_lines", "expected_separator_id"),
    [
        ((2, 3), None),
        ((2, 5), 1),
    ],
)
def test_worktree_discard_uses_source_alternative_as_cleanup_evidence(
    selected_source_lines,
    expected_separator_id,
):
    """A tail spliced from disjoint source spans consumes its separator."""
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(0, 0, 1, 3),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b""),
            LineEntry(2, "+", None, 2, text_bytes=b"selected one"),
            LineEntry(3, "+", None, 3, text_bytes=b"selected two"),
        ],
    )
    source_lines = [
        b"\n",
        b"selected one\n",
        b"selected two\n",
        b"stale sibling\n",
        b"selected two\n",
    ]
    selected_presence = LineRanges.from_lines(selected_source_lines)
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", source_lines, space=BatchSourceSpace),
        [
            line.with_source_line(source_line)
            for line, source_line in zip(
                line_changes.lines[1:],
                selected_source_lines,
                strict=True,
            )
        ],
        resolve_presence_source_alternatives(
            selected_presence,
            source_lines,
        ),
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        {2, 3},
        [b"\n", b"selected one\n", b"selected two\n"],
        source_selection,
    )

    assert discard_selection.owned_blank_id is None
    assert discard_selection.cleanup_blank_id == expected_separator_id


def test_worktree_discard_removes_separator_before_a_peeled_source_suffix():
    """A current tail still inside the source consumes its leading blank."""
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(0, 0, 1, 2),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b""),
            LineEntry(2, "+", None, 2, text_bytes=b"selected"),
        ],
    )
    source_lines = [b"\n", b"selected\n", b"peeled sibling\n"]
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", source_lines, space=BatchSourceSpace),
        [line_changes.lines[1].with_source_line(2)],
        last_nonblank_source_line=3,
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        {2},
        [b"\n", b"selected\n"],
        source_selection,
    )

    assert discard_selection.cleanup_blank_id == 1


def test_worktree_discard_removes_separator_after_split_selected_block():
    """A split source block does not leave its following blank in the worktree."""
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(0, 0, 1, 5),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"heading"),
            LineEntry(2, "+", None, 2, text_bytes=b"selected one"),
            LineEntry(3, "+", None, 3, text_bytes=b"selected two"),
            LineEntry(4, "+", None, 4, text_bytes=b""),
            LineEntry(5, "+", None, 5, text_bytes=b"next block"),
        ],
    )
    source_lines = [
        b"heading\n",
        b"selected one\n",
        b"selected two\n",
        b"\n",
        b"next block\n",
    ]
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", source_lines, space=BatchSourceSpace),
        [
            line_changes.lines[1].with_source_line(2),
            line_changes.lines[2].with_source_line(3),
            line_changes.lines[3].with_source_line(4),
        ],
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        {2, 3},
        [
            b"heading\n",
            b"selected one\n",
            b"selected two\n",
            b"\n",
            b"next block\n",
        ],
        source_selection,
    )

    assert discard_selection.owned_blank_id == 4
    assert discard_selection.cleanup_blank_id is None


def test_worktree_discard_collapses_blanks_around_removed_added_block():
    """Removing an added block should leave one of its two separators."""
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(0, 0, 1, 5),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"before"),
            LineEntry(2, "+", None, 2, text_bytes=b""),
            LineEntry(3, "+", None, 3, text_bytes=b"selected"),
            LineEntry(4, "+", None, 4, text_bytes=b""),
            LineEntry(5, "+", None, 5, text_bytes=b"after"),
        ],
    )
    working_lines = [
        b"before\n",
        b"\n",
        b"selected\n",
        b"\n",
        b"after\n",
    ]
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", working_lines, space=BatchSourceSpace),
        [line_changes.lines[2].with_source_line(3)],
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        {3},
        working_lines,
        source_selection,
    )

    assert discard_selection.owned_blank_id is None
    assert discard_selection.cleanup_blank_id == 4


def test_worktree_discard_preserves_blank_from_tracked_content():
    """A retained blank beside an added block is not redundant ownership."""
    line_changes = LineLevelChange(
        path="module.c",
        header=HunkHeader(3, 3, 3, 5),
        lines=[
            LineEntry(None, " ", 3, 3, text_bytes=b"before"),
            LineEntry(None, " ", 4, 4, text_bytes=b""),
            LineEntry(1, "+", None, 5, text_bytes=b"selected"),
            LineEntry(2, "+", None, 6, text_bytes=b""),
            LineEntry(None, " ", 5, 7, text_bytes=b"after"),
        ],
    )
    working_lines = [
        b"prefix one\n",
        b"prefix two\n",
        b"before\n",
        b"\n",
        b"selected\n",
        b"\n",
        b"after\n",
    ]
    source_selection = SourceBoundLineSelection(
        content_snapshot("module.c", working_lines, space=BatchSourceSpace),
        [line_changes.lines[2].with_source_line(5)],
    )

    discard_selection = build_worktree_discard_selection(
        line_changes,
        {1},
        working_lines,
        source_selection,
    )

    assert discard_selection.owned_blank_id is None
    assert discard_selection.cleanup_blank_id is None
