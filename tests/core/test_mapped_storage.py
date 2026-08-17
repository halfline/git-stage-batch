"""Tests for mapped storage primitives."""

from __future__ import annotations

import mmap
import os

import pytest

import git_stage_batch.core.mapped_storage as mapped_storage_module
from git_stage_batch.core.mapped_storage import (
    ChunkedMappedRecordVector,
    ManagedMappedResources,
    MappedIntVector,
    MappedRecordVector,
    byte_storage_from_chunks,
    byte_storage_from_path,
)


class _CopyFailingBytearray(bytearray):
    """Bytearray test double that fails if converted through bytes()."""

    def __bytes__(self):
        raise AssertionError("chunk should stream without being copied")


class _CloseCountingMappedResource:
    """Mapped-resource double with an injectable close cancellation."""

    def __init__(
        self,
        *,
        byte_count: int = 8,
        error: BaseException | None = None,
    ) -> None:
        self.byte_count = byte_count
        self.error = error
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1
        if self.error is not None:
            raise self.error


def _open_fd_count() -> int | None:
    fd_path = "/proc/self/fd"
    if not os.path.isdir(fd_path):
        return None
    return len(os.listdir(fd_path))


def test_byte_storage_from_path_uses_heap_below_threshold(tmp_path, monkeypatch):
    """Small path byte storage should stay heap-backed."""

    def fail_temporary_file(*args, **kwargs):
        raise AssertionError("small path storage should use heap storage")

    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        fail_temporary_file,
    )

    file_path = tmp_path / "small.txt"
    file_path.write_bytes(b"alpha\n")

    data, file_handle = byte_storage_from_path(file_path)

    assert file_handle is None
    assert data == b"alpha\n"
    assert not isinstance(data, mmap.mmap)


def test_byte_storage_from_path_uses_mapped_storage_at_threshold(tmp_path):
    """Page-sized path byte storage should use mapped storage."""
    file_path = tmp_path / "page.txt"
    file_path.write_bytes(b"x" * mmap.PAGESIZE)

    data, file_handle = byte_storage_from_path(file_path)
    try:
        assert isinstance(data, mmap.mmap)
        assert file_handle is not None
        assert data[:4] == b"xxxx"
    finally:
        data.close()
        assert file_handle is not None
        file_handle.close()


def test_byte_storage_from_path_closes_handle_on_cancellation(
    tmp_path,
    monkeypatch,
):
    """Cancellation during path inspection must release the opened handle."""
    file_path = tmp_path / "cancelled.txt"
    file_path.write_bytes(b"content\n")
    file_handle = file_path.open("rb")

    class CancellingPath:
        def open(self, _mode):
            return file_handle

        def stat(self):
            raise KeyboardInterrupt("path inspection cancelled")

    monkeypatch.setattr(
        mapped_storage_module,
        "Path",
        lambda _path: CancellingPath(),
    )

    with pytest.raises(KeyboardInterrupt, match="inspection cancelled"):
        byte_storage_from_path(file_path)

    assert file_handle.closed is True


def test_byte_storage_from_chunks_copies_small_mutable_chunks():
    """Small chunk byte storage should copy mutable chunks."""
    chunk = bytearray(b"alpha\n")

    data, file_handle = byte_storage_from_chunks([chunk])
    chunk[:] = b"omega\n"

    assert file_handle is None
    assert data == b"alpha\n"


def test_byte_storage_from_chunks_streams_large_chunks_without_copying():
    """Large chunk byte storage should stream chunks directly."""
    prefix = b"alpha\n"
    threshold_chunk = _CopyFailingBytearray(b"x" * (mmap.PAGESIZE - len(prefix)))
    remaining_chunk = _CopyFailingBytearray(b"omega\n")

    data, file_handle = byte_storage_from_chunks(
        [
            prefix,
            threshold_chunk,
            remaining_chunk,
        ]
    )
    try:
        assert isinstance(data, mmap.mmap)
        assert file_handle is not None
        assert data[: len(prefix)] == prefix
        assert data[-len(remaining_chunk) :] == bytes(bytearray(remaining_chunk))
    finally:
        data.close()
        assert file_handle is not None
        file_handle.close()


def test_byte_storage_from_chunks_closes_spill_handle_on_cancellation(
    tmp_path,
    monkeypatch,
):
    """Cancellation while streaming chunks must release mapped spill state."""
    file_handle = (tmp_path / "spill").open("w+b")

    def chunks():
        yield b"x" * mmap.PAGESIZE
        raise KeyboardInterrupt("chunk stream cancelled")

    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        lambda _spool_dir=None: file_handle,
    )

    with pytest.raises(KeyboardInterrupt, match="stream cancelled"):
        byte_storage_from_chunks(chunks())

    assert file_handle.closed is True


def test_mapped_int_vector_get_set_fill_and_close():
    """Mapped integer vectors expose fixed-width unsigned slots."""
    vector = MappedIntVector(4, width=4, fill=7)

    assert list(vector) == [7, 7, 7, 7]

    vector[1] = 9
    assert vector[1] == 9

    vector.fill(3)
    assert list(vector) == [3, 3, 3, 3]

    with pytest.raises(OverflowError):
        vector[0] = -1

    vector.close()
    vector.close()
    with pytest.raises(ValueError, match="closed"):
        vector[0]


def test_less_than_page_mapped_int_vector_uses_heap(monkeypatch):
    """Integer vectors smaller than one memory page should stay heap-backed."""

    def fail_temporary_file(*args, **kwargs):
        raise AssertionError("small vector should use heap storage")

    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        fail_temporary_file,
    )

    vector = MappedIntVector(4, width=4, fill=7)

    assert vector.byte_count < mmap.PAGESIZE
    assert list(vector) == [7, 7, 7, 7]


def test_page_sized_mapped_int_vector_uses_mmap(monkeypatch):
    """Page-sized integer vectors still use temporary mmap storage."""
    calls = 0
    original_temporary_file = mapped_storage_module.tempfile.TemporaryFile

    def counting_temporary_file(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        counting_temporary_file,
    )

    with MappedIntVector(mmap.PAGESIZE // 8, width=8, fill=3) as vector:
        assert vector.byte_count == mmap.PAGESIZE
        assert vector[0] == 3

    assert calls == 1


def test_mapped_vector_closes_spill_handle_when_mmap_is_cancelled(
    tmp_path,
    monkeypatch,
):
    """Cancellation during mmap setup must release the allocated spill file."""
    file_handle = (tmp_path / "vector-spill").open("w+b")

    monkeypatch.setattr(
        mapped_storage_module,
        "_temporary_file",
        lambda _spool_dir=None: file_handle,
    )

    def cancel_mmap(*_args, **_kwargs):
        raise KeyboardInterrupt("mmap cancelled")

    monkeypatch.setattr(
        mapped_storage_module.mmap,
        "mmap",
        cancel_mmap,
    )

    with pytest.raises(KeyboardInterrupt, match="mmap cancelled"):
        MappedIntVector(mmap.PAGESIZE // 8)

    assert file_handle.closed is True


def test_mapped_vector_closes_spill_handle_when_mmap_close_fails():
    """A busy mmap must not prevent its sibling file handle from closing."""
    vector = MappedIntVector(mmap.PAGESIZE // 8)
    data = vector._data
    file_handle = vector._file_handle
    assert isinstance(data, mmap.mmap)
    assert file_handle is not None
    exported_view = memoryview(data)

    try:
        with pytest.raises(BufferError):
            vector.close()
        assert file_handle.closed is True
        assert vector._closed is False
    finally:
        exported_view.release()

    vector.close()
    assert vector._closed is True


def test_mapped_vector_finalizer_suppresses_cancellation(monkeypatch):
    """A finalizer must never leak a cleanup BaseException."""
    vector = MappedIntVector(0)

    def cancel_close():
        raise KeyboardInterrupt("finalizer cancelled")

    monkeypatch.setattr(vector, "close", cancel_close)

    vector.__del__()


def test_mapped_storage_uses_dynamic_default_scratch_parent(
    tmp_path,
    monkeypatch,
):
    """Implicit mapped spill files should use the large-scratch default."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    directories = []
    original_temporary_file = mapped_storage_module.tempfile.TemporaryFile

    def recording_temporary_file(*args, **kwargs):
        directories.append(kwargs.get("dir"))
        return original_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        mapped_storage_module,
        "default_scratch_parent",
        lambda: scratch,
    )
    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        recording_temporary_file,
    )

    with MappedIntVector(mmap.PAGESIZE // 8) as vector:
        assert vector.byte_count == mmap.PAGESIZE

    assert directories == [scratch]


def test_explicit_mapped_storage_spool_precedes_default(
    tmp_path,
    monkeypatch,
):
    """A caller-owned spool directory should retain explicit placement."""
    spool = tmp_path / "spool"
    spool.mkdir()
    directories = []
    original_temporary_file = mapped_storage_module.tempfile.TemporaryFile

    def recording_temporary_file(*args, **kwargs):
        directories.append(kwargs.get("dir"))
        return original_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        mapped_storage_module,
        "default_scratch_parent",
        lambda: pytest.fail("explicit spool should bypass the default"),
    )
    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        recording_temporary_file,
    )

    with MappedIntVector(
        mmap.PAGESIZE // 8,
        spool_dir=spool,
    ) as vector:
        assert vector.byte_count == mmap.PAGESIZE

    assert directories == [spool]


def test_mapped_int_vector_uses_64_bit_slots():
    """Mapped integer vectors store values past the 32-bit range."""
    value = (1 << 40) + 3

    with MappedIntVector(1, width=8) as vector:
        vector[0] = value
        assert vector[0] == value


def test_mapped_record_vector_append_and_indexed_write():
    """Mapped record vectors support append and pre-sized writes."""
    records = MappedRecordVector(3, "QQ")

    records.append((1, 2))
    records.append((3, 4))
    assert records[0] == (1, 2)
    assert list(records) == [(1, 2), (3, 4)]

    records[1] = (5, 6)
    assert records[1] == (5, 6)

    with pytest.raises(IndexError):
        records[2]

    records.close()
    with pytest.raises(ValueError, match="closed"):
        len(records)


def test_less_than_page_mapped_record_vector_uses_heap(monkeypatch):
    """Record vectors smaller than one memory page should stay heap-backed."""

    def fail_temporary_file(*args, **kwargs):
        raise AssertionError("small record vector should use heap storage")

    monkeypatch.setattr(
        mapped_storage_module.tempfile,
        "TemporaryFile",
        fail_temporary_file,
    )

    records = MappedRecordVector(3, "QQ")
    records.append((1, 2))

    assert records.byte_count < mmap.PAGESIZE
    assert records[0] == (1, 2)


def test_mapped_record_vector_can_start_presized():
    """Pre-sized record vectors allow indexed population."""
    with MappedRecordVector(2, "QQ", length=2) as records:
        records[0] = (10, 20)
        records[1] = (30, 40)
        assert list(records) == [(10, 20), (30, 40)]


def test_sort_mapped_records_returns_early_for_ordered_records(monkeypatch):
    """Already ordered mapped records should need only a linear scan."""
    with MappedRecordVector(3, "QQ") as records:
        records.append((1, 2))
        records.append((1, 3))
        records.append((2, 1))
        monkeypatch.setattr(
            mapped_storage_module,
            "_sift_mapped_record",
            lambda *_args: pytest.fail("ordered records must not build a heap"),
        )

        mapped_storage_module.sort_mapped_records(records)

        assert list(records) == [(1, 2), (1, 3), (2, 1)]


def test_sort_mapped_records_still_orders_unsorted_records():
    """The ordered fast path must retain bounded in-place sorting fallback."""
    with MappedRecordVector(3, "QQ") as records:
        records.append((2, 1))
        records.append((1, 3))
        records.append((1, 2))

        mapped_storage_module.sort_mapped_records(records)

        assert list(records) == [(1, 2), (1, 3), (2, 1)]


def test_chunked_mapped_record_vector_grows_from_small_chunks():
    """Chunked vectors should avoid allocating the full chunk up front."""
    records = ChunkedMappedRecordVector(
        record_format="QQ",
        chunk_capacity=4,
    )

    for value in range(8):
        records.append((value, value + 10))

    byte_count = records.byte_count

    assert byte_count < mmap.PAGESIZE
    assert records._chunk_starts == [0, 1, 3, 7]
    assert records[0] == (0, 10)
    assert records[1] == (1, 11)
    assert records[2] == (2, 12)
    assert records[3] == (3, 13)
    assert records[6] == (6, 16)
    assert records[7] == (7, 17)

    records.close()


def test_chunked_vector_failed_growth_keeps_index_state_atomic(monkeypatch):
    """Cancelled chunk allocation cannot leave a phantom chunk start."""
    records = ChunkedMappedRecordVector(
        record_format="QQ",
        chunk_capacity=4,
    )

    with monkeypatch.context() as patch:
        patch.setattr(
            mapped_storage_module,
            "MappedRecordVector",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("chunk allocation cancelled")
            ),
        )
        with pytest.raises(KeyboardInterrupt, match="chunk allocation cancelled"):
            records.append((1, 2))

    assert records._chunk_starts == []
    assert records._chunks == []
    assert len(records) == 0
    records.append((1, 2))
    assert records[0] == (1, 2)
    records.close()


def test_chunked_mapped_record_vector_closes_later_chunks_after_cancellation():
    """One chunk close cancellation must not skip the remaining chunks."""
    records = ChunkedMappedRecordVector(
        record_format="QQ",
        chunk_capacity=4,
    )
    cancelled = _CloseCountingMappedResource(
        error=KeyboardInterrupt("chunk close cancelled")
    )
    later = _CloseCountingMappedResource()
    records._chunks.extend((cancelled, later))

    with pytest.raises(KeyboardInterrupt, match="chunk close cancelled"):
        records.close()

    assert cancelled.close_count == 1
    assert later.close_count == 1
    assert records.closed is False

    cancelled.error = None
    records.close()
    assert records.closed is True


def test_mapped_record_append_rolls_back_length_after_post_write_cancellation(
    monkeypatch,
):
    """A completed packed write is not visible when append reports failure."""
    records = MappedRecordVector(2, "QQ")
    original_write = records._write_record

    def write_then_cancel(index, record):
        original_write(index, record)
        raise KeyboardInterrupt("record write cancelled")

    monkeypatch.setattr(records, "_write_record", write_then_cancel)
    with pytest.raises(KeyboardInterrupt, match="record write cancelled"):
        records.append((1, 2))

    assert len(records) == 0
    records.close()


def test_chunked_append_rolls_back_inner_record_after_cancellation(monkeypatch):
    """Chunk and aggregate lengths must advance as one append transaction."""
    records = ChunkedMappedRecordVector(record_format="QQ", chunk_capacity=4)
    original_append = MappedRecordVector.append

    def append_then_cancel(self, record):
        original_append(self, record)
        raise KeyboardInterrupt("chunk append cancelled")

    monkeypatch.setattr(MappedRecordVector, "append", append_then_cancel)
    with pytest.raises(KeyboardInterrupt, match="chunk append cancelled"):
        records.append((1, 2))

    assert len(records) == 0
    assert records._chunks == []
    assert records._chunk_starts == []
    records.close()


def test_managed_mapped_resources_close_all_after_cancellation():
    """Workspace teardown must attempt every resource and remain retryable."""
    resources = ManagedMappedResources()
    earlier = resources.track(_CloseCountingMappedResource())
    cancelled = resources.track(
        _CloseCountingMappedResource(
            error=KeyboardInterrupt("workspace close cancelled")
        )
    )

    with pytest.raises(KeyboardInterrupt, match="workspace close cancelled"):
        resources.close()

    assert earlier.close_count == 1
    assert cancelled.close_count == 1
    assert resources._current_bytes == 16
    assert len(resources._resources) == 2

    cancelled.error = None
    resources.close()
    assert resources._current_bytes == 0
    assert resources._resources == []


def test_managed_mapped_resources_close_untracked_resource_when_tracking_fails():
    """A failed ownership handoff must close the resource being transferred."""

    class CancellingResourceList(list):
        def append(self, _resource):
            raise KeyboardInterrupt("tracking cancelled")

    resources = ManagedMappedResources()
    resources._resources = CancellingResourceList()
    resource = _CloseCountingMappedResource()

    with pytest.raises(KeyboardInterrupt, match="tracking cancelled"):
        resources.track(resource)

    assert resource.close_count == 1
    assert resources._current_bytes == 0
    resources.close()


def test_managed_mapped_resource_close_failure_remains_tracked():
    """A failed individual close must retain accounting for a later retry."""
    resources = ManagedMappedResources()
    cancelled = resources.track(
        _CloseCountingMappedResource(
            error=KeyboardInterrupt("resource close cancelled")
        )
    )

    with pytest.raises(KeyboardInterrupt, match="resource close cancelled"):
        resources.close_resource(cancelled)

    assert resources._resources == [cancelled]
    assert resources._current_bytes == 8

    cancelled.error = None
    resources.close_resource(cancelled)
    assert resources._resources == []
    assert resources._current_bytes == 0


def test_managed_resource_scope_ignores_an_outer_exception_handler():
    """Successful early release must not inherit a caller's handled exception."""
    resources = ManagedMappedResources()
    resource = resources.track(_CloseCountingMappedResource())

    try:
        raise RuntimeError("outer")
    except RuntimeError:
        with resources.release_resource_on_exit(resource) as acquired:
            assert acquired is resource

    assert resource.close_count == 1
    assert resources._resources == []
    assert resources._current_bytes == 0
    resources.close()


def test_mapped_context_preserves_body_error_when_close_fails(monkeypatch):
    """Mapped cleanup cancellation must not hide the primary operation error."""
    vector = MappedIntVector(1)
    real_close = vector.close

    def fail_close():
        raise RuntimeError("mapped close failed")

    monkeypatch.setattr(vector, "close", fail_close)
    try:
        with pytest.raises(KeyboardInterrupt, match="operation cancelled"):
            with vector:
                raise KeyboardInterrupt("operation cancelled")
    finally:
        monkeypatch.setattr(vector, "close", real_close)
        vector.close()


def test_repeated_vector_open_close_does_not_leak_file_descriptors():
    """Mapped vectors close their temporary file descriptors."""
    before = _open_fd_count()

    for _ in range(25):
        with MappedIntVector(8, width=8) as vector:
            vector.fill(4)

    after = _open_fd_count()
    if before is not None and after is not None:
        assert after <= before + 2
