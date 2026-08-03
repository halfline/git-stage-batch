"""Tests for replacement-selection command helpers."""

import pytest

from git_stage_batch.commands.selection.replacement_selection import (
    expand_replacement_selection_ids,
    require_contiguous_display_selection,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.exceptions import CommandError


def test_contiguous_display_selection_accepts_adjacent_ids():
    """Adjacent selected display IDs should pass replacement validation."""
    require_contiguous_display_selection({2, 3, 4})


def test_contiguous_display_selection_rejects_gapped_ids():
    """Gapped selected display IDs should fail replacement validation."""
    with pytest.raises(CommandError) as exc_info:
        require_contiguous_display_selection({2, 4})

    assert "Replacement selection must be one contiguous line range." in (
        exc_info.value.message
    )


@pytest.mark.parametrize(
    "requested_ids",
    [{3}, {3, 4}, {1, 2}],
)
def test_replacement_selection_expands_both_complete_sides(requested_ids):
    """Selecting either side of a replacement includes the full mixed run."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 2, 1, 2),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "-", 2, None, text_bytes=b"old-b"),
            LineEntry(3, "+", None, 1, text_bytes=b"new-a"),
            LineEntry(4, "+", None, 2, text_bytes=b"new-b"),
        ],
    )

    assert expand_replacement_selection_ids(line_changes, requested_ids) == {
        1,
        2,
        3,
        4,
    }


def test_replacement_selection_leaves_surplus_additions_outside_core():
    """An insertion following a one-for-one replacement remains independently selectable."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 2),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 1, text_bytes=b"new"),
            LineEntry(3, "+", None, 2, text_bytes=b"extra"),
        ],
    )

    assert expand_replacement_selection_ids(line_changes, {2}) == {1, 2}
    assert expand_replacement_selection_ids(line_changes, {3}) == {3}
