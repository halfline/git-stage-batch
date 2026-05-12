"""Tests for editor line editing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import overload

import pytest

from git_stage_batch.editor import (
    EditorBuffer,
    Editor,
    edit_lines_as_buffer,
    export_lines_as_buffer,
)


class _LengthGuardedSequence(Sequence[bytes]):
    """Sequence that fails if code asks for full length up front."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        raise AssertionError("source length should not be required")

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> list[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | list[bytes]:
        return self._lines[index]


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


def test_export_lines_as_buffer_exports_generated_lines():
    """Generated line exports render without editor selection state."""
    lines = (line for line in [b"one\r\n", b"two\r"])

    with export_lines_as_buffer(lines, has_trailing_newline=True) as buffer:
        assert isinstance(buffer, EditorBuffer)
        assert buffer.to_bytes() == b"one\ntwo\r\n"


def test_export_lines_as_buffer_keeps_empty_output_empty():
    """Trailing-newline state does not create content for empty output."""
    with export_lines_as_buffer([], has_trailing_newline=True) as buffer:
        assert buffer.to_bytes() == b""


def test_export_lines_as_buffer_restores_line_endings(line_sequence):
    """Generated line exports can follow another buffer's line endings."""
    line_endings_source = line_sequence([b"base\r\n"])

    with export_lines_as_buffer(
        [b"one\n", b"two\n"],
        has_trailing_newline=True,
        line_endings_from=line_endings_source,
    ) as buffer:
        assert buffer.to_bytes() == b"one\r\ntwo\r\n"


def test_editor_replaces_selected_lines(line_sequence):
    """Editor selections can be replaced with generated lines."""
    source_lines = line_sequence([b"one\r\n", b"two\r\n", b"three\r\n"])

    with Editor(source_lines) as editor:
        editor.move_to(1)
        editor.select_lines(1)
        editor.add_lines(line for line in [b"new\r\n", b"again\r"])

        with editor.export(has_trailing_newline=True) as buffer:
            assert buffer.to_bytes() == b"one\nnew\nagain\nthree\n"


def test_editor_removes_selected_lines(line_sequence):
    """Editor selections can be removed."""
    source_lines = line_sequence([
        b"one\n",
        b"two\n",
        b"three\n",
        b"four\n",
    ])

    with Editor(source_lines) as editor:
        editor.move_to(1)
        editor.select_to(3)
        editor.remove()

        with editor.export(has_trailing_newline=True) as buffer:
            assert buffer.to_bytes() == b"one\nfour\n"


def test_editor_cursor_rides_after_inserted_lines(line_sequence):
    """Cursors at an insertion point move after inserted lines."""
    source_lines = line_sequence([b"one\n", b"two\n"])

    with Editor(source_lines) as editor:
        cursor = editor.cursor_at(1)
        editor.move_to(1)
        editor.add_lines([b"inserted", b"again"])
        editor.move_to(cursor)

        assert editor.position == 3

        editor.add_line(b"after")

        with editor.export(has_trailing_newline=True) as buffer:
            assert buffer.to_bytes() == b"one\ninserted\nagain\nafter\ntwo\n"


def test_editor_source_line_cursor_tracks_replacement(line_sequence):
    """Source line cursors track destination shifts after replacement edits."""
    source_lines = line_sequence([b"one\n", b"two\n", b"three\n"])

    with Editor(source_lines) as editor:
        before_third = editor.cursor_at_source_line(2)
        editor.move_to(1)
        editor.select_lines(1)
        editor.add_lines([b"new two", b"extra"])
        editor.move_to(before_third)

        assert editor.position == 3

        editor.add_line(b"before three")

        with editor.export(has_trailing_newline=True) as buffer:
            assert (
                buffer.to_bytes()
                == b"one\nnew two\nextra\nbefore three\nthree\n"
            )


def test_editor_add_bytes_splits_raw_bytes(line_sequence):
    """Raw bytes are inserted as split lines."""
    source_lines = line_sequence([])

    with Editor(source_lines) as editor:
        editor.add_bytes(b"one\r\ntwo")

        with editor.export(
            has_trailing_newline=False,
            add_trailing_newline_when_nonempty=True,
        ) as buffer:
            assert buffer.to_bytes() == b"one\ntwo\n"


def test_editor_add_bytes_keeps_bare_cr_as_content(line_sequence):
    """Raw byte insertion uses Git-coordinate LF line boundaries."""
    source_lines = line_sequence([])

    with Editor(source_lines) as editor:
        editor.add_bytes(b"one\rtwo\r")

        with editor.export(has_trailing_newline=False) as buffer:
            assert buffer.to_bytes() == b"one\rtwo\r"


def test_editor_defers_source_length_for_positioned_insert():
    """Editor does not need full source length for a positioned insert."""
    source_lines = _LengthGuardedSequence([b"one\n", b"two\n", b"three\n"])

    with Editor(source_lines) as editor:
        editor.move_to(1)
        editor.add_line(b"inserted")

        with editor.export(has_trailing_newline=True) as buffer:
            assert buffer.to_bytes() == b"one\ninserted\ntwo\nthree\n"


def test_editor_transform_receives_normalized_selected_lines(line_sequence):
    """Transform handlers receive normalized selected lines."""
    source_lines = line_sequence([b"one\r\n", b"two\r\n", b"three\r\n"])

    def replace(selected_lines: Sequence[bytes]) -> list[bytes]:
        assert list(selected_lines) == [b"two\n"]
        return [b"new\n", b"again\r\n"]

    with Editor(source_lines) as editor:
        editor.move_to(1)
        editor.select_lines(1)
        editor.transform(replace)

        with editor.export(
            has_trailing_newline=True,
            line_endings_from=source_lines,
        ) as buffer:
            assert buffer.to_bytes() == b"one\r\nnew\r\nagain\r\nthree\r\n"


def test_editor_transform_accepts_bytes_result(line_sequence):
    """Transform handlers can return raw bytes."""
    source_lines = line_sequence([b"one\r\n", b"two\r\n"])

    with Editor(source_lines) as editor:
        editor.move_to(1)
        editor.select_lines(1)
        editor.transform(lambda selected_lines: b"new\r\nagain")

        with editor.export(
            has_trailing_newline=True,
            line_endings_from=source_lines,
        ) as buffer:
            assert buffer.to_bytes() == b"one\r\nnew\r\nagain\r\n"


def test_editor_select_all_transform_defers_source_length():
    """Whole-buffer transforms can stream selected lines."""
    source_lines = _LengthGuardedSequence([b"one\r\n", b"two\r\n"])

    with Editor(source_lines) as editor:
        editor.select_all()
        editor.transform(lambda selected_lines: selected_lines)

        with editor.export(
            has_trailing_newline=True,
            line_endings_from=source_lines,
        ) as buffer:
            assert buffer.to_bytes() == b"one\r\ntwo\r\n"


def test_editor_selected_line_slices_are_lazy_sequences():
    """Selected-line slices do not materialize lists."""
    source_lines = _LengthGuardedSequence([
        b"one\n",
        b"two\n",
        b"three\n",
    ])

    def first_two(selected_lines: Sequence[bytes]) -> Sequence[bytes]:
        selected_slice = selected_lines[:2]
        assert isinstance(selected_slice, Sequence)
        assert not isinstance(selected_slice, list)
        return selected_slice

    with Editor(source_lines) as editor:
        editor.select_all()
        editor.transform(first_two)

        with editor.export(has_trailing_newline=True) as buffer:
            assert buffer.to_bytes() == b"one\ntwo\n"


def test_editor_remove_requires_selection(line_sequence):
    """Removing without a selected range is rejected."""
    source_lines = line_sequence([b"one\n"])

    with Editor(source_lines) as editor:
        with pytest.raises(ValueError, match="no line selection"):
            editor.remove()


def test_editor_export_freezes_editor(line_sequence):
    """Exporting a buffer ends the editor session."""
    source_lines = line_sequence([b"one\n"])

    with Editor(source_lines) as editor:
        buffer = editor.export(has_trailing_newline=True)

        with buffer:
            assert buffer.to_bytes() == b"one\n"

        with pytest.raises(ValueError, match="editor is closed"):
            editor.add_line(b"two")
