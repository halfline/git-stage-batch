"""Tests for line buffer loading."""

from __future__ import annotations

import mmap
import os
import stat
from collections.abc import Sequence

import pytest

from git_stage_batch.core import buffer as buffer_module
from git_stage_batch.core.buffer import (
    LineBuffer,
    buffer_byte_chunks,
    buffer_byte_count,
    buffer_ends_with_lf,
    buffer_has_data,
    buffer_matches,
    buffer_preview,
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)


class _CopyFailingBytearray(bytearray):
    """Bytearray test double that fails if converted through bytes()."""

    def __bytes__(self):
        raise AssertionError("chunk should stream without being copied")


def test_line_buffer_indexes_in_memory_lines():
    """In-memory buffer exposes Git-coordinate byte lines by index."""
    buffer = LineBuffer.from_bytes(b"one\ntwo\r\nthree\rfour")

    assert buffer.byte_count == 19
    assert len(buffer) == 3
    assert buffer[0] == b"one\n"
    assert buffer[1] == b"two\r\n"
    assert buffer[2] == b"three\rfour"
    assert buffer[-1] == b"three\rfour"
    assert buffer[1:3] == [b"two\r\n", b"three\rfour"]


def test_line_buffer_slices_are_lazy_sequences():
    """Buffer slices expose indexed views without materializing lists."""
    buffer = LineBuffer.from_bytes(b"zero\none\ntwo\nthree\n")

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


def test_line_buffer_acquires_scoped_line_views():
    """Acquired line sequences expose bytes-compatible scoped views."""
    with LineBuffer.from_bytes(b"alpha\nbeta\r\n") as buffer:
        with buffer.acquire_lines() as lines:
            first = lines[0]
            matching = lines[0]
            second = lines[1]

            assert len(lines) == 2
            assert len(first) == len(b"alpha\n")
            assert not isinstance(first, bytes)
            assert first == b"alpha\n"
            assert b"alpha\n" == first
            assert first == matching
            assert first != second
            assert hash(first) == hash(b"alpha\n")
            assert bytes(first) == b"alpha\n"
            assert first[0] == ord("a")
            assert first[:-1] == b"alpha"
            assert first.endswith(b"\n")
            assert first.endswith((b"\r\n", b"\n"))
            assert list(lines[0:2]) == [b"alpha\n", b"beta\r\n"]


def test_line_buffer_acquires_single_scoped_line_view():
    """Single-line acquisition supports negative indexes."""
    with LineBuffer.from_bytes(b"alpha\nbeta\n") as buffer:
        with buffer.acquire_line(-1) as line:
            assert not isinstance(line, bytes)
            assert line == b"beta\n"


def test_line_buffer_slices_acquire_scoped_line_views():
    """Buffer slices forward scoped line acquisition to their parent."""
    with LineBuffer.from_bytes(b"zero\none\ntwo\nthree\n") as buffer:
        sliced = buffer[-3:-1]

        with sliced.acquire_lines() as lines:
            first = lines[0]
            nested = lines[1:]

            assert len(lines) == 2
            assert not isinstance(first, bytes)
            assert first == b"one\n"
            assert list(lines) == [b"one\n", b"two\n"]
            assert list(nested) == [b"two\n"]

        with pytest.raises(ValueError, match="line view is closed"):
            bytes(first)


def test_line_buffer_line_views_use_acquisition_lifetime():
    """Line views reject access after their acquisition scope closes."""
    with LineBuffer.from_bytes(b"alpha\n") as buffer:
        with buffer.acquire_line(0) as line:
            assert bytes(line) == b"alpha\n"

        with pytest.raises(ValueError, match="line view is closed"):
            bytes(line)
        with pytest.raises(ValueError, match="line view is closed"):
            len(line)
        with pytest.raises(ValueError, match="line view is closed"):
            hash(line)


def test_line_buffer_acquired_line_views_do_not_hold_mmap_exports(tmp_path):
    """Acquired line views release temporary memoryviews before scope exit."""
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(b"alpha\nbeta\n")
    buffer = LineBuffer.from_path(file_path)

    with buffer.acquire_line(0) as line:
        assert line == b"alpha\n"
        assert hash(line) == hash(b"alpha\n")

    buffer.close()

    with pytest.raises(ValueError, match="line view is closed"):
        bytes(line)


def test_line_buffer_slice_uses_parent_lifetime():
    """Buffer slices depend on the parent buffer remaining open."""
    buffer = LineBuffer.from_bytes(b"one\ntwo\n")
    sliced = buffer[0:1]

    buffer.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        _ = sliced[0]


def test_line_buffer_iterates_byte_chunks():
    """Buffer exposes byte chunks without changing line indexing."""
    buffer = LineBuffer.from_bytes(b"alpha\nbeta\ngamma")

    assert list(buffer.byte_chunks(6)) == [b"alpha\n", b"beta\ng", b"amma"]
    assert buffer[1] == b"beta\n"


def test_line_buffer_handles_empty_buffer():
    """Empty buffer has no byte lines."""
    buffer = LineBuffer.from_bytes(b"")

    assert buffer.byte_count == 0
    assert list(buffer.byte_chunks()) == []
    assert len(buffer) == 0
    with pytest.raises(IndexError):
        _ = buffer[0]


def test_buffer_has_data_checks_buffer_entries():
    """Empty and non-empty buffers can be distinguished."""
    with (
        LineBuffer.from_bytes(b"") as empty,
        LineBuffer.from_bytes(b"alpha") as non_empty,
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


def test_buffer_ends_with_lf_accepts_buffer_inputs(line_sequence):
    """Trailing newline checks should use buffer bytes instead of line indexing."""
    assert buffer_ends_with_lf(b"alpha\n") is True
    assert buffer_ends_with_lf(b"alpha") is False
    assert buffer_ends_with_lf(b"") is False
    assert buffer_ends_with_lf(line_sequence([b"alpha", b"\n"])) is True

    with LineBuffer.from_chunks([b"alpha", b"\nbeta"]) as buffer:
        assert buffer_ends_with_lf(buffer) is False


def test_buffer_helpers_accept_buffers(tmp_path):
    """Buffer helpers can stream buffers to a file."""
    output_path = tmp_path / "out.txt"

    with LineBuffer.from_chunks([b"alpha\n", b"beta\n"]) as buffer:
        assert list(buffer_byte_chunks(buffer, 6)) == [b"alpha\n", b"beta\n"]
        assert buffer_byte_count(buffer) == 11
        assert buffer_preview(buffer, 8) == b"alpha\nbe"

        write_buffer_to_path(output_path, buffer)

    assert output_path.read_bytes() == b"alpha\nbeta\n"


def test_write_buffer_failure_preserves_existing_file(tmp_path):
    """A failed streaming write must not publish partial contents."""
    output_path = tmp_path / "out.txt"
    output_path.write_bytes(b"original\n")

    def failing_chunks():
        yield b"replacement prefix\n"
        raise RuntimeError("simulated stream failure")

    with pytest.raises(RuntimeError, match="simulated stream failure"):
        write_buffer_to_path(output_path, failing_chunks())

    assert output_path.read_bytes() == b"original\n"
    assert list(tmp_path.glob(".git-stage-batch-*.tmp")) == []


def test_regular_write_supports_maximum_length_filename(tmp_path):
    """Atomic regular-file publication must use a short private filename."""
    maximum_name_length = os.pathconf(tmp_path, "PC_NAME_MAX")
    output_path = tmp_path / ("x" * maximum_name_length)
    output_path.write_bytes(b"old contents")

    write_buffer_to_path(output_path, b"new contents")

    assert output_path.read_bytes() == b"new contents"


def test_symlink_write_keeps_old_entry_until_atomic_replace(tmp_path, monkeypatch):
    """Publishing a new symlink target must not expose a missing path."""
    output_path = tmp_path / "link"
    os.symlink(b"old-target", os.fsencode(output_path))
    original_replace = os.replace

    def inspect_replace(source, destination):
        assert destination == output_path
        assert output_path.is_symlink()
        assert os.readlink(output_path) == "old-target"
        original_replace(source, destination)

    monkeypatch.setattr(buffer_module.os, "replace", inspect_replace)

    write_buffer_to_path(output_path, b"new-target")

    assert output_path.is_symlink()
    assert os.readlink(output_path) == "new-target"


def test_failed_symlink_publication_preserves_old_entry(tmp_path, monkeypatch):
    """A failed atomic publish must leave the existing symlink intact."""
    output_path = tmp_path / "link"
    os.symlink(b"old-target", os.fsencode(output_path))

    def fail_replace(*args):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(buffer_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated publication failure"):
        write_buffer_to_path(output_path, b"new-target")

    assert output_path.is_symlink()
    assert os.readlink(output_path) == "old-target"
    assert list(tmp_path.glob(".git-stage-batch-*.tmp")) == []


def test_symlink_write_supports_maximum_length_filename(tmp_path):
    """The private publication name must not depend on user filename length."""
    maximum_name_length = os.pathconf(tmp_path, "PC_NAME_MAX")
    output_path = tmp_path / ("x" * maximum_name_length)
    os.symlink(b"old-target", os.fsencode(output_path))

    write_buffer_to_path(output_path, b"new-target")

    assert output_path.is_symlink()
    assert os.readlink(output_path) == "new-target"


def test_worktree_symlink_replaces_regular_file_atomically(tmp_path):
    """Git symlink mode atomically replaces a current regular file."""
    output_path = tmp_path / "path"
    output_path.write_bytes(b"regular contents")

    write_buffer_to_working_tree_path(output_path, b"target", mode="120000")

    assert output_path.is_symlink()
    assert os.readlink(output_path) == "target"


def test_worktree_regular_file_replaces_symlink_atomically(tmp_path):
    """Git regular-file mode replaces the symlink, not its referent."""
    output_path = tmp_path / "path"
    referent = tmp_path / "referent"
    referent.write_bytes(b"referent contents")
    os.symlink(referent.name, output_path)

    write_buffer_to_working_tree_path(output_path, b"regular", mode="100644")

    assert not output_path.is_symlink()
    assert output_path.read_bytes() == b"regular"
    assert referent.read_bytes() == b"referent contents"


def test_atomic_regular_write_tightens_mode_when_ownership_cannot_be_preserved(
    tmp_path,
    monkeypatch,
):
    """A differently owned replacement must not retain group or other access."""
    if not hasattr(buffer_module.os, "fchown"):
        pytest.skip("fchown is not available")

    output_path = tmp_path / "path"
    output_path.write_bytes(b"old")
    output_path.chmod(0o666)

    def fail_fchown(*_args):
        raise PermissionError("ownership cannot be preserved")

    monkeypatch.setattr(buffer_module.os, "fchown", fail_fchown)

    write_buffer_to_path(output_path, b"new")

    assert output_path.read_bytes() == b"new"
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


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
        LineBuffer.from_chunks([b"alpha", b"\nbeta\n"]) as left,
        LineBuffer.from_bytes(b"alpha\nbeta\n") as right,
    ):
        assert buffer_matches(left, right) is True


def test_line_buffer_uses_heap_for_files_smaller_than_memory_page(tmp_path):
    """Small file buffers stay on the Python heap."""
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(b"alpha\nbeta\n")

    with LineBuffer.from_path(file_path) as buffer:
        assert buffer.uses_mapped_storage is False
        assert buffer.byte_count == len(b"alpha\nbeta\n")
        assert list(buffer.byte_chunks(5)) == [b"alpha", b"\nbeta", b"\n"]
        assert buffer.to_bytes() == b"alpha\nbeta\n"
        assert len(buffer) == 2
        assert buffer[1] == b"beta\n"


def test_line_buffer_uses_mapped_storage_for_page_sized_files(tmp_path):
    """Page-sized file buffers use mapped storage."""
    data = b"alpha\n" + b"x" * (mmap.PAGESIZE - len(b"alpha\n"))
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(data)

    with LineBuffer.from_path(file_path) as buffer:
        assert buffer.uses_mapped_storage is True
        assert buffer.byte_count == mmap.PAGESIZE
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"x" * (mmap.PAGESIZE - len(b"alpha\n"))


def test_line_buffer_clone_survives_original_close():
    """A clone retains shared heap storage after its original closes."""
    original = LineBuffer.from_bytes(b"alpha\nbeta\n")
    clone = original.clone()

    original.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        _ = original[0]
    assert clone[0] == b"alpha\n"
    assert clone[1] == b"beta\n"
    clone.close()


def test_line_buffer_original_survives_clone_close():
    """Closing a clone does not release storage retained by its original."""
    original = LineBuffer.from_bytes(b"alpha\nbeta\n")
    clone = original.clone()

    clone.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        _ = clone[0]
    assert original[0] == b"alpha\n"
    assert original[1] == b"beta\n"
    original.close()


def test_line_buffer_clones_have_independent_line_indexes():
    """Clones share bytes without sharing mutable line-scan state."""
    original = LineBuffer.from_bytes(b"alpha\nbeta\ngamma\n")
    clone = original.clone()

    assert original[0] == b"alpha\n"
    assert clone[2] == b"gamma\n"
    assert original._line_span_count() == 1
    assert clone._line_span_count() == 3

    original.close()
    clone.close()


def test_line_buffer_mapped_backing_closes_after_final_clone(tmp_path):
    """Mapped storage and its file remain open until every clone closes."""
    data = b"alpha\n" + b"x" * (mmap.PAGESIZE - len(b"alpha\n"))
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(data)
    original = LineBuffer.from_path(file_path)
    first_clone = original.clone()
    final_clone = original.clone()
    mapped_data = original._data
    file_handle = original._backing.file_handle

    assert isinstance(mapped_data, mmap.mmap)
    assert file_handle is not None
    assert original._backing is first_clone._backing
    assert original._backing is final_clone._backing

    original.close()
    first_clone.close()

    assert mapped_data.closed is False
    assert file_handle.closed is False
    assert final_clone[0] == b"alpha\n"

    final_clone.close()

    assert mapped_data.closed is True
    assert file_handle.closed is True


def test_line_buffer_skips_mapped_storage_for_empty_files(tmp_path):
    """Empty files do not use mapped storage but still expose an empty buffer."""
    file_path = tmp_path / "empty.txt"
    file_path.write_bytes(b"")

    with LineBuffer.from_path(file_path) as buffer:
        assert buffer.uses_mapped_storage is False
        assert len(buffer) == 0


def test_line_buffer_uses_heap_for_generated_chunks_smaller_than_page():
    """Small generated buffers stay on the Python heap."""
    chunks = iter([b"alpha\nbe", b"ta\n", memoryview(b"gamma")])

    with LineBuffer.from_chunks(chunks) as buffer:
        assert buffer.uses_mapped_storage is False
        assert len(buffer) == 3
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"beta\n"
        assert buffer[2] == b"gamma"


def test_line_buffer_copies_small_generated_chunks_for_heap_storage():
    """Small mutable generated chunks are copied before storage."""
    chunk = bytearray(b"alpha\n")

    with LineBuffer.from_chunks([chunk]) as buffer:
        chunk[:] = b"omega\n"
        assert buffer.to_bytes() == b"alpha\n"


def test_line_buffer_spools_page_sized_generated_chunks_to_mapped_storage():
    """Page-sized generated buffers are spooled to mapped storage."""
    prefix = b"alpha\nbeta\n"
    chunks = iter([
        prefix[:7],
        prefix[7:],
        memoryview(b"x" * (mmap.PAGESIZE - len(prefix))),
    ])

    with LineBuffer.from_chunks(chunks) as buffer:
        assert buffer.uses_mapped_storage is True
        assert buffer.byte_count == mmap.PAGESIZE
        assert buffer[0] == b"alpha\n"
        assert buffer[1] == b"beta\n"
        assert buffer[2] == b"x" * (mmap.PAGESIZE - len(prefix))


def test_line_buffer_streams_threshold_chunk_without_copying():
    """The chunk that reaches the mapped-storage threshold streams directly."""
    prefix = b"alpha\n"
    threshold_chunk = _CopyFailingBytearray(
        b"x" * (mmap.PAGESIZE - len(prefix))
    )

    with LineBuffer.from_chunks([prefix, threshold_chunk]) as buffer:
        assert buffer.uses_mapped_storage is True
        assert buffer.byte_count == mmap.PAGESIZE
        assert buffer[0] == prefix
        assert buffer[1] == bytes(bytearray(threshold_chunk))


def test_line_buffer_streams_remaining_large_chunks_without_copying():
    """Chunks after the mapped-storage threshold stream directly."""
    threshold_chunk = b"x" * mmap.PAGESIZE
    remaining_chunk = _CopyFailingBytearray(b"omega\n")

    with LineBuffer.from_chunks([threshold_chunk, remaining_chunk]) as buffer:
        assert buffer.uses_mapped_storage is True
        assert buffer.byte_count == mmap.PAGESIZE + len(remaining_chunk)
        assert buffer[0] == threshold_chunk + b"omega\n"


def test_line_buffer_handles_empty_generated_chunks():
    """Empty generated buffers have no byte lines."""
    with LineBuffer.from_chunks([]) as buffer:
        assert buffer.uses_mapped_storage is False
        assert len(buffer) == 0


def test_line_buffer_rejects_non_byte_chunks():
    """Generated buffers must yield bytes-like chunks."""
    with pytest.raises(TypeError, match="expected bytes-like object"):
        LineBuffer.from_chunks([b"ok\n", "not bytes"])


def test_line_buffer_rejects_non_positive_chunk_size():
    """Chunk iteration requires a positive chunk size."""
    buffer = LineBuffer.from_bytes(b"alpha")

    with pytest.raises(ValueError, match="chunk size must be positive"):
        list(buffer.byte_chunks(0))


def test_line_buffer_close_is_idempotent(tmp_path):
    """Closed buffers reject later access."""
    file_path = tmp_path / "buffer.txt"
    file_path.write_bytes(b"alpha\n")
    buffer = LineBuffer.from_path(file_path)

    buffer.close()
    buffer.close()

    with pytest.raises(ValueError, match="buffer is closed"):
        len(buffer)
