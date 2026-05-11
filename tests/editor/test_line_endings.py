"""Tests for editor line-ending helpers."""

from __future__ import annotations

from git_stage_batch.editor import (
    EditorBuffer,
    choose_line_ending,
    detect_line_ending,
    restore_line_endings,
    restore_line_endings_in_chunks,
)


def test_detect_line_ending_reads_bytes_and_buffer():
    """Line endings can be detected from bytes or buffers."""
    assert detect_line_ending(b"one\r\ntwo\n") == b"\r\n"
    assert detect_line_ending(b"one\rtwo\n") == b"\n"

    with EditorBuffer.from_bytes(b"one\rtwo\n") as buffer:
        assert detect_line_ending(buffer) == b"\n"

    with EditorBuffer.from_bytes(b"one\rtwo\r") as buffer:
        assert detect_line_ending(buffer) == b"\r"


def test_choose_line_ending_uses_first_buffer_with_an_ending():
    """A fallback buffer can provide the output line ending."""
    with (
        EditorBuffer.from_bytes(b"alpha") as first,
        EditorBuffer.from_bytes(b"beta\ngamma\n") as second,
    ):
        assert choose_line_ending(first, second) == b"\n"


def test_restore_line_endings_rewrites_normalized_output():
    """Normalized LF output can be written with the selected line ending."""
    assert restore_line_endings(b"one\ntwo\n", b"\r\n") == b"one\r\ntwo\r\n"
    assert restore_line_endings(b"one\ntwo\n", b"\n") == b"one\ntwo\n"
    assert restore_line_endings(b"one\ntwo\n", None) == b"one\ntwo\n"


def test_restore_line_endings_in_chunks_rewrites_each_chunk():
    """Chunked LF output can be written with the selected line ending."""
    chunks = restore_line_endings_in_chunks(
        [b"one\n", b"two\nthree\n"],
        b"\r\n",
    )

    assert list(chunks) == [b"one\r\n", b"two\r\nthree\r\n"]
