"""Tests for editor buffer loading."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from git_stage_batch.editor import (
    EditorBuffer,
    buffer_byte_chunks,
    buffer_byte_count,
    buffer_has_data,
    buffer_matches,
    buffer_preview,
    write_buffer_to_path,
)


def test_editor_buffer_indexes_in_memory_lines():
    """In-memory buffer exposes Git-coordinate byte lines by index."""
    buffer = EditorBuffer.from_bytes(b"one\ntwo\r\nthree\rfour")

    assert buffer.byte_count == 19
    assert len(buffer) == 3
    assert buffer[0] == b"one\n"
    assert buffer[1] == b"two\r\n"
    assert buffer[2] == b"three\rfour"
    assert buffer[-1] == b"three\rfour"
    assert buffer[1:3] == [b"two\r\n", b"three\rfour"]


def test_editor_buffer_slices_are_lazy_sequences():
    """Buffer slices expose indexed views without materializing lists."""
    buffer = EditorBuffer.from_bytes(b"zero\none\ntwo\nthree\n")

    sliced = buffer[1:4]

    assert isinstance(sliced, Sequence)
    assert not isinstance(sliced, list)
    assert len(sliced) == 3
    assert sliced[0] == b"one\n"
    assert sliced[-1] == b"three\n"
    assert list(sliced) == [b"one\n", b"two\n", b"three\n"]

    nested = sliced[1:]

    assert isinstance(nested, Sequence)
    assert not isinstance(nested, list)
    assert list(nested) == [b"two\n", b"three\n"]


def test_editor_buffer_slice_uses_parent_lifetime():
    """Buffer slices depend on the parent buffer remaining open."""
    buffer = EditorBuffer.from_bytes(b"one\ntwo\n")
    sliced = buffer[0:1]

    buffer.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        _ = sliced[0]


def test_editor_buffer_iterates_byte_chunks():
    """Buffer exposes byte chunks without changing line indexing."""
    buffer = EditorBuffer.from_bytes(b"alpha\nbeta\ngamma")

    assert list(buffer.byte_chunks(6)) == [b"alpha\n", b"beta\ng", b"amma"]
    assert buffer[1] == b"beta\n"


def test_editor_buffer_handles_empty_buffer():
    """Empty buffer has no byte lines."""
    buffer = EditorBuffer.from_bytes(b"")

    assert buffer.byte_count == 0
    assert list(buffer.byte_chunks()) == []
    assert len(buffer) == 0
    with pytest.raises(IndexError):
        _ = buffer[0]


def test_buffer_has_data_checks_buffer_entries():
    """Empty and non-empty buffers can be distinguished."""
    with (
        EditorBuffer.from_bytes(b"") as empty,
        EditorBuffer.from_bytes(b"alpha") as non_empty,
    ):
        assert buffer_has_data(empty) is False
        assert buffer_has_data(non_empty) is True


def test_buffer_helpers_accept_in_memory_bytes():
    """Buffer helpers accept existing bytes."""
    assert list(buffer_byte_chunks(b"alpha")) == [b"alpha"]
    assert buffer_byte_count(b"alpha") == 5
    assert buffer_preview(b"alphabet", 5) == b"alpha"


def test_buffer_helpers_accept_line_sequences(line_sequence):
    """Buffer helpers treat line sequences as buffer chunks."""
    buffer = line_sequence([b"alpha\n", b"beta\n"])

    assert list(buffer_byte_chunks(buffer)) == [b"alpha\n", b"beta\n"]
    assert buffer_byte_count(buffer) == 11
    assert buffer_preview(buffer, 8) == b"alpha\nbe"


def test_buffer_helpers_accept_buffers(tmp_path):
    """Buffer helpers can stream buffers to a file."""
    output_path = tmp_path / "out.txt"

    with EditorBuffer.from_chunks([b"alpha\n", b"beta\n"]) as buffer:
        assert list(buffer_byte_chunks(buffer, 6)) == [b"alpha\n", b"beta\n"]
        assert buffer_byte_count(buffer) == 11
        assert buffer_preview(buffer, 8) == b"alpha\nbe"

        write_buffer_to_path(output_path, buffer)

    assert output_path.read_bytes() == b"alpha\nbeta\n"


def test_buffer_matches_across_chunk_boundaries(line_sequence):
    """Buffer comparison ignores how inputs are chunked."""
    left = line_sequence([b"alpha\n", b"beta\n"])
    right = [b"alpha", b"\nbeta", b"\n"]

    assert buffer_matches(left, right) is True
    assert buffer_matches(left, b"alpha\nbeta\n") is True
    assert buffer_matches(left, b"alpha\ngamma\n") is False


def test_buffer_matches_buffers():
    """Buffer comparison accepts buffers."""
    with (
        EditorBuffer.from_chunks([b"alpha", b"\nbeta\n"]) as left,
        EditorBuffer.from_bytes(b"alpha\nbeta\n") as right,
    ):
        assert buffer_matches(left, right) is True


def test_editor_buffer_uses_mmap_for_non_empty_files(tmp_path):
    """Non-empty file buffers are backed by mmap."""
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(b"alpha\nbeta\n")

    with EditorBuffer.from_path(file_path) as buffer:
        assert buffer.is_mmap_backed is True
        assert buffer.byte_count == len(b"alpha\nbeta\n")
        assert list(buffer.byte_chunks(5)) == [b"alpha", b"\nbeta", b"\n"]
        assert buffer.to_bytes() == b"alpha\nbeta\n"
        assert len(buffer) == 2
        assert buffer[1] == b"beta\n"


def test_editor_buffer_skips_mmap_for_empty_files(tmp_path):
    """Empty files cannot be mmap-backed but still expose an empty buffer."""
    file_path = tmp_path / "empty.txt"
    file_path.write_bytes(b"")

    with EditorBuffer.from_path(file_path) as buffer:
        assert buffer.is_mmap_backed is False
        assert len(buffer) == 0


def test_editor_buffer_spools_generated_chunks_to_mmap():
    """Generated chunks are spooled to an mmap-backed buffer."""
    chunks = iter([b"alpha\nbe", b"ta\n", memoryview(b"gamma")])

    with EditorBuffer.from_chunks(chunks) as buffer:
        assert buffer.is_mmap_backed is True
        assert len(buffer) == 3
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"beta\n"
        assert buffer[2] == b"gamma"


def test_editor_buffer_handles_empty_generated_chunks():
    """Empty generated buffers have no byte lines."""
    with EditorBuffer.from_chunks([]) as buffer:
        assert buffer.is_mmap_backed is False
        assert len(buffer) == 0


def test_editor_buffer_rejects_non_byte_chunks():
    """Generated buffers must yield bytes-like chunks."""
    with pytest.raises(TypeError, match="expected bytes-like object"):
        EditorBuffer.from_chunks([b"ok\n", "not bytes"])


def test_editor_buffer_rejects_non_positive_chunk_size():
    """Chunk iteration requires a positive chunk size."""
    buffer = EditorBuffer.from_bytes(b"alpha")

    with pytest.raises(ValueError, match="chunk size must be positive"):
        list(buffer.byte_chunks(0))


def test_editor_buffer_close_is_idempotent(tmp_path):
    """Closed buffers reject later access."""
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(b"alpha\n")
    buffer = EditorBuffer.from_path(file_path)

    buffer.close()
    buffer.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        len(buffer)
