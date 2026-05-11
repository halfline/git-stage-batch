"""Tests for editor line editing."""

from __future__ import annotations

import pytest

from git_stage_batch.editor import (
    EditorBuffer,
    edit_lines_as_buffer,
)


def test_edit_lines_as_buffer_replaces_middle_lines(line_sequence):
    """Line edits render normalized LF content."""
    source_lines = line_sequence([b"one\r\n", b"two\r\n", b"three\r\n"])

    with edit_lines_as_buffer(
        source_lines,
        [b"new"],
        selection_start=1,
        selection_end=2,
        has_trailing_newline=True,
    ) as buffer:
        assert isinstance(buffer, EditorBuffer)
        assert buffer.to_bytes() == b"one\nnew\nthree\n"


def test_edit_lines_as_buffer_strips_edited_line_endings(line_sequence):
    """Edited lines can include their own line endings."""
    source_lines = line_sequence([b"one\n", b"two\n", b"three\n"])

    with edit_lines_as_buffer(
        source_lines,
        [b"new\r\n", b"again\r"],
        selection_start=1,
        selection_end=2,
        has_trailing_newline=True,
    ) as buffer:
        assert buffer.to_bytes() == b"one\nnew\nagain\nthree\n"


def test_edit_lines_as_buffer_preserves_missing_final_newline(
    line_sequence,
):
    """A source without a final newline stays without one."""
    source_lines = line_sequence([b"one\n", b"two"])

    with edit_lines_as_buffer(
        source_lines,
        [b"new"],
        selection_start=1,
        selection_end=2,
        has_trailing_newline=False,
    ) as buffer:
        assert buffer.to_bytes() == b"one\nnew"


def test_edit_lines_as_buffer_can_insert_at_empty_selection(line_sequence):
    """Empty selections insert lines."""
    source_lines = line_sequence([])

    with edit_lines_as_buffer(
        source_lines,
        [b"new"],
        selection_start=0,
        selection_end=0,
        has_trailing_newline=False,
        add_trailing_newline_when_nonempty=True,
    ) as buffer:
        assert buffer.to_bytes() == b"new\n"


def test_edit_lines_as_buffer_validates_selection(line_sequence):
    """Invalid line selections are rejected before rendering."""
    source_lines = line_sequence([b"one\n"])

    with pytest.raises(ValueError, match="invalid line selection"):
        edit_lines_as_buffer(
            source_lines,
            [],
            selection_start=2,
            selection_end=1,
            has_trailing_newline=True,
        )
