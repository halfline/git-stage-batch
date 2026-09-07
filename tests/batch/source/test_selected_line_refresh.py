"""Tests for worktree line views used by selected-line refresh."""

import pytest

from git_stage_batch.batch.source.selected_line_refresh import _WorkingLineRun


@pytest.mark.parametrize(
    "selection",
    [
        slice(None, None, 2),
        slice(None, None, -1),
        slice(None, None, -2),
        slice(1, 4, 2),
        slice(20, None, 2),
        slice(2, 2, -1),
    ],
    ids=["stride", "reverse", "reverse-stride", "bounded", "past-end", "empty"],
)
def test_working_line_run_slices_match_list(selection):
    """Slicing a line view follows list semantics, including nested slices."""
    lines = [b"before\n", b"one\n", b"two\n", b"three\n", b"four\n", b"after\n"]
    view = _WorkingLineRun(lines, 1, 4)
    expected = lines[1:5][selection]
    selected = view[selection]

    assert list(selected) == expected
    assert len(selected) == len(expected)
    assert list(selected[::-1]) == expected[::-1]
    assert list(selected[::2]) == expected[::2]
    if expected:
        assert selected[-1] == expected[-1]
    with pytest.raises(IndexError):
        selected[len(expected)]
