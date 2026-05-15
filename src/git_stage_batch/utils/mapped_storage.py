"""Storage helpers that use mmap for larger allocations."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Iterator, Sequence
import mmap
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO


_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_FILL_CHUNK_BYTES = 1024 * 1024
MAPPED_STORAGE_OFFLOAD_SIZE_THRESHOLD = mmap.PAGESIZE
_BytesLike = bytes | bytearray | memoryview
_ByteStorage = bytes | mmap.mmap
_StorageBuffer = bytearray | mmap.mmap


def should_use_mapped_storage(byte_count: int) -> bool:
    """Return whether a byte count should use mapped storage."""
    return byte_count >= MAPPED_STORAGE_OFFLOAD_SIZE_THRESHOLD


def byte_storage_from_path(path: str | Path) -> tuple[_ByteStorage, BinaryIO | None]:
    """Load path bytes using heap memory below the mapped-storage threshold."""
    file_path = Path(path)
    file_handle = file_path.open("rb")
    try:
        byte_count = file_path.stat().st_size
        if byte_count == 0:
            file_handle.close()
            return b"", None
        if not should_use_mapped_storage(byte_count):
            data = file_handle.read()
            file_handle.close()
            return data, None

        return (
            mmap.mmap(file_handle.fileno(), 0, access=mmap.ACCESS_READ),
            file_handle,
        )
    except Exception:
        file_handle.close()
        raise


def byte_storage_from_chunks(
    chunks: Iterable[_BytesLike],
    *,
    spool_dir: str | Path | None = None,
) -> tuple[_ByteStorage, BinaryIO | None]:
    """Build byte storage from chunks using mapped storage above the threshold."""
    pending_chunks: list[bytes] = []
    byte_count = 0
    chunk_iter = iter(chunks)

    for chunk in chunk_iter:
        chunk = _validate_byte_chunk(chunk)
        chunk_size = _chunk_byte_count(chunk)
        if chunk_size == 0:
            continue
        next_byte_count = byte_count + chunk_size
        if should_use_mapped_storage(next_byte_count):
            return _byte_storage_from_chunk_prefix_and_remainder(
                pending_chunks,
                chunk,
                chunk_iter,
                spool_dir=spool_dir,
            )

        byte_count = next_byte_count
        pending_chunks.append(bytes(chunk))

    if byte_count == 0:
        return b"", None
    return b"".join(pending_chunks), None


def _validate_byte_chunk(chunk: _BytesLike) -> _BytesLike:
    if isinstance(chunk, bytes):
        return chunk
    if isinstance(chunk, (bytearray, memoryview)):
        return chunk
    raise TypeError(f"expected bytes-like object, got {type(chunk).__name__}")


def _chunk_byte_count(chunk: _BytesLike) -> int:
    if isinstance(chunk, memoryview):
        return chunk.nbytes
    return len(chunk)


def _byte_storage_from_chunk_prefix_and_remainder(
    pending_chunks: Sequence[bytes],
    threshold_chunk: _BytesLike,
    remaining_chunks: Iterator[_BytesLike],
    *,
    spool_dir: str | Path | None = None,
) -> tuple[_ByteStorage, BinaryIO | None]:
    file_handle = _temporary_file(spool_dir)
    try:
        for chunk in pending_chunks:
            file_handle.write(chunk)
        file_handle.write(threshold_chunk)
        for chunk in remaining_chunks:
            chunk = _validate_byte_chunk(chunk)
            file_handle.write(chunk)
        file_handle.flush()
        return (
            mmap.mmap(file_handle.fileno(), 0, access=mmap.ACCESS_READ),
            file_handle,
        )
    except Exception:
        file_handle.close()
        raise


def _temporary_file(spool_dir: str | Path | None = None) -> BinaryIO:
    return tempfile.TemporaryFile(
        dir=None if spool_dir is None else Path(spool_dir)
    )


def _normalize_width(width: int) -> int:
    if width not in (4, 8):
        raise ValueError("integer width must be 4 or 8 bytes")
    return width


def _typecode_for_width(width: int) -> str:
    return "I" if width == 4 else "Q"


def _format_for_width(width: int) -> str:
    return "<I" if width == 4 else "<Q"


def _normalize_record_format(record_format: str) -> str:
    if not record_format:
        raise ValueError("record format must not be empty")
    if record_format[0] not in "@=<>!":
        return "<" + record_format
    return record_format


def _max_for_width(width: int) -> int:
    return _UINT32_MAX if width == 4 else _UINT64_MAX


def _check_unsigned(value: int, max_value: int) -> None:
    if value < 0 or value > max_value:
        raise OverflowError("value does not fit in unsigned storage")


def _allocate_storage(
    byte_count: int,
    *,
    spool_dir: str | Path | None = None,
) -> tuple[_StorageBuffer | None, BinaryIO | None]:
    if byte_count <= 0:
        return None, None
    if not should_use_mapped_storage(byte_count):
        return bytearray(byte_count), None

    file_handle = _temporary_file(spool_dir)
    try:
        file_handle.truncate(byte_count)
        return mmap.mmap(file_handle.fileno(), byte_count), file_handle
    except Exception:
        file_handle.close()
        raise


def _close_storage(
    data: _StorageBuffer | None,
    file_handle: BinaryIO | None,
) -> None:
    if isinstance(data, mmap.mmap):
        data.close()
    if file_handle is not None:
        file_handle.close()


class MappedIntVector(Sequence[int]):
    """Fixed-width unsigned integer vector."""

    def __init__(
        self,
        length: int,
        *,
        width: int = 8,
        fill: int = 0,
        spool_dir: str | Path | None = None,
    ) -> None:
        if length < 0:
            raise ValueError("length must be non-negative")

        self._width = _normalize_width(width)
        self._length = length
        self._format = struct.Struct(_format_for_width(self._width))
        self._max_value = _max_for_width(self._width)
        self._byte_count = length * self._width
        self._file_handle: BinaryIO | None = None
        self._data: _StorageBuffer | None = None
        self._closed = False

        _check_unsigned(fill, self._max_value)
        self._data, self._file_handle = _allocate_storage(
            self._byte_count,
            spool_dir=spool_dir,
        )
        if self._data is not None and fill != 0:
            self.fill(fill)

    @property
    def typecode(self) -> str:
        """Return an array-compatible unsigned integer type code."""
        return _typecode_for_width(self._width)

    @property
    def byte_count(self) -> int:
        """Return allocated storage bytes."""
        return 0 if self._closed else self._byte_count

    @property
    def closed(self) -> bool:
        """Return whether the vector has been closed."""
        return self._closed

    def __len__(self) -> int:
        self._require_open()
        return self._length

    def __getitem__(self, index: int | slice) -> int | list[int]:
        self._require_open()
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(self._length))]

        index = self._normalize_index(index)
        data = self._require_storage()
        return self._format.unpack_from(data, index * self._width)[0]

    def __setitem__(self, index: int, value: int) -> None:
        self._require_open()
        index = self._normalize_index(index)
        _check_unsigned(value, self._max_value)
        data = self._require_storage()
        self._format.pack_into(data, index * self._width, value)

    def fill(self, value: int) -> None:
        """Set every slot to one unsigned value."""
        self._require_open()
        _check_unsigned(value, self._max_value)
        if self._length == 0:
            return

        data = self._require_storage()
        packed = self._format.pack(value)
        repeat = max(1, _FILL_CHUNK_BYTES // self._width)
        chunk = packed * min(self._length, repeat)
        offset = 0
        remaining = self._length

        while remaining:
            count = min(remaining, len(chunk) // self._width)
            byte_count = count * self._width
            data[offset:offset + byte_count] = chunk[:byte_count]
            offset += byte_count
            remaining -= count

    def close(self) -> None:
        """Close storage resources."""
        if self._closed:
            return

        _close_storage(self._data, self._file_handle)
        self._data = None
        self._file_handle = None
        self._closed = True

    def __enter__(self) -> MappedIntVector:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _normalize_index(self, index: int) -> int:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return index

    def _require_storage(self) -> _StorageBuffer:
        if self._data is None:
            raise IndexError("empty vector has no storage")
        return self._data

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("mapped integer vector is closed")


class MappedRecordVector(Sequence[tuple[int, ...]]):
    """Fixed-size record vector."""

    def __init__(
        self,
        capacity: int,
        record_format: str,
        *,
        length: int | None = None,
        spool_dir: str | Path | None = None,
    ) -> None:
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        if length is None:
            length = 0
        if length < 0 or length > capacity:
            raise ValueError("length must fit within capacity")

        self._struct = struct.Struct(_normalize_record_format(record_format))
        self._capacity = capacity
        self._length = length
        self._byte_count = capacity * self._struct.size
        self._file_handle: BinaryIO | None = None
        self._data: _StorageBuffer | None = None
        self._closed = False

        self._data, self._file_handle = _allocate_storage(
            self._byte_count,
            spool_dir=spool_dir,
        )

    @property
    def capacity(self) -> int:
        """Return the maximum record count."""
        self._require_open()
        return self._capacity

    @property
    def record_size(self) -> int:
        """Return bytes per record."""
        return self._struct.size

    @property
    def byte_count(self) -> int:
        """Return allocated storage bytes."""
        return 0 if self._closed else self._byte_count

    @property
    def closed(self) -> bool:
        """Return whether the vector has been closed."""
        return self._closed

    def __len__(self) -> int:
        self._require_open()
        return self._length

    def __getitem__(self, index: int | slice) -> tuple[int, ...] | list[tuple[int, ...]]:
        self._require_open()
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(self._length))]

        index = self._normalize_index(index)
        data = self._require_storage()
        return self._struct.unpack_from(data, index * self._struct.size)

    def __setitem__(self, index: int, record: Sequence[int]) -> None:
        self._require_open()
        index = self._normalize_index(index)
        self._write_record(index, record)

    def append(self, record: Sequence[int]) -> int:
        """Append a record and return its zero-based index."""
        self._require_open()
        if self._length >= self._capacity:
            raise OverflowError("record vector capacity exceeded")

        index = self._length
        self._write_record(index, record)
        self._length += 1
        return index

    def fill(self, record: Sequence[int]) -> None:
        """Set every existing record to one value."""
        self._require_open()
        for index in range(self._length):
            self._write_record(index, record)

    def close(self) -> None:
        """Close storage resources."""
        if self._closed:
            return

        _close_storage(self._data, self._file_handle)
        self._data = None
        self._file_handle = None
        self._closed = True

    def __enter__(self) -> MappedRecordVector:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _write_record(self, index: int, record: Sequence[int]) -> None:
        data = self._require_storage()
        self._struct.pack_into(data, index * self._struct.size, *record)

    def _normalize_index(self, index: int) -> int:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return index

    def _require_storage(self) -> _StorageBuffer:
        if self._data is None:
            raise IndexError("empty record vector has no storage")
        return self._data

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("mapped record vector is closed")


class ChunkedMappedRecordVector(Sequence[tuple[int, ...]]):
    """Append-only record vector that grows by fixed-width chunks."""

    def __init__(
        self,
        *,
        record_format: str,
        chunk_capacity: int,
        spool_dir: str | Path | None = None,
    ) -> None:
        if chunk_capacity <= 0:
            raise ValueError("chunk capacity must be positive")

        self._record_format = record_format
        self._chunk_capacity = chunk_capacity
        self._spool_dir = spool_dir
        self._chunks: list[MappedRecordVector] = []
        self._chunk_starts: list[int] = []
        self._next_chunk_capacity = 1
        self._length = 0
        self._closed = False

    @property
    def byte_count(self) -> int:
        """Return allocated storage bytes across chunks."""
        if self._closed:
            return 0
        return sum(chunk.byte_count for chunk in self._chunks)

    @property
    def closed(self) -> bool:
        """Return whether the vector has been closed."""
        return self._closed

    def __len__(self) -> int:
        self._require_open()
        return self._length

    def __getitem__(self, index: int | slice) -> tuple[int, ...] | list[tuple[int, ...]]:
        self._require_open()
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(self._length))]

        index = self._normalize_index(index)
        chunk, record_index = self._chunk_for_index(index)
        return chunk[record_index]

    def append(self, record: Sequence[int]) -> int:
        """Append a record and return its zero-based index."""
        self._require_open()
        if not self._chunks or len(self._chunks[-1]) >= self._chunks[-1].capacity:
            self._append_chunk()

        index = self._length
        self._chunks[-1].append(record)
        self._length += 1
        return index

    def _append_chunk(self) -> None:
        capacity = self._next_chunk_capacity
        self._chunk_starts.append(self._length)
        self._chunks.append(
            MappedRecordVector(
                capacity,
                self._record_format,
                spool_dir=self._spool_dir,
            )
        )
        self._next_chunk_capacity = min(
            self._chunk_capacity,
            max(capacity + 1, capacity * 2),
        )

    def _chunk_for_index(self, index: int) -> tuple[MappedRecordVector, int]:
        chunk_index = bisect_right(self._chunk_starts, index) - 1
        if chunk_index < 0:
            raise IndexError(index)
        return (
            self._chunks[chunk_index],
            index - self._chunk_starts[chunk_index],
        )

    def close(self) -> None:
        """Close every allocated chunk."""
        if self._closed:
            return

        for chunk in self._chunks:
            chunk.close()
        self._chunks.clear()
        self._chunk_starts.clear()
        self._closed = True

    def __enter__(self) -> ChunkedMappedRecordVector:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _normalize_index(self, index: int) -> int:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return index

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("chunked mapped record vector is closed")


class ManagedMappedResources:
    """Track storage resources and byte high-water use."""

    def __init__(self) -> None:
        self._resources: list[object] = []
        self._current_bytes = 0
        self._high_water_bytes = 0
        self._total_allocated_bytes = 0
        self._closed = False

    @property
    def current_bytes(self) -> int:
        """Return bytes currently owned by open tracked resources."""
        return self._current_bytes

    @property
    def high_water_bytes(self) -> int:
        """Return the highest simultaneously tracked byte count."""
        return self._high_water_bytes

    @property
    def total_allocated_bytes(self) -> int:
        """Return total bytes ever allocated through this manager."""
        return self._total_allocated_bytes

    def track(self, resource: object) -> object:
        """Track a storage resource for deterministic cleanup."""
        self._require_open()
        self._resources.append(resource)
        byte_count = _resource_byte_count(resource)
        self._current_bytes += byte_count
        self._total_allocated_bytes += byte_count
        self._high_water_bytes = max(self._high_water_bytes, self._current_bytes)
        return resource

    def close_resource(self, resource: object) -> None:
        """Close one tracked resource and update current byte accounting."""
        if resource not in self._resources:
            _close_resource(resource)
            return

        self._resources.remove(resource)
        byte_count = _resource_byte_count(resource)
        _close_resource(resource)
        self._current_bytes = max(0, self._current_bytes - byte_count)

    def close(self) -> None:
        """Close all tracked resources."""
        if self._closed:
            return

        for resource in reversed(self._resources):
            _close_resource(resource)
        self._resources.clear()
        self._current_bytes = 0
        self._closed = True

    def __enter__(self) -> ManagedMappedResources:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("mapped resource manager is closed")


def _resource_byte_count(resource: object) -> int:
    byte_count = getattr(resource, "byte_count", 0)
    if isinstance(byte_count, int):
        return byte_count
    return 0


def _close_resource(resource: object) -> None:
    close = getattr(resource, "close", None)
    if close is not None:
        close()
