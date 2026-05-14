"""Tempfile-backed fixed-width storage helpers."""

from __future__ import annotations

from collections.abc import Sequence
import mmap
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO


_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_FILL_CHUNK_BYTES = 1024 * 1024


def _normalize_width(width: int) -> int:
    if width not in (4, 8):
        raise ValueError("integer width must be 4 or 8 bytes")
    return width


def _typecode_for_width(width: int) -> str:
    return "I" if width == 4 else "Q"


def _format_for_width(width: int) -> str:
    return "<I" if width == 4 else "<Q"


def _max_for_width(width: int) -> int:
    return _UINT32_MAX if width == 4 else _UINT64_MAX


def _check_unsigned(value: int, max_value: int) -> None:
    if value < 0 or value > max_value:
        raise OverflowError("value does not fit in unsigned storage")


class MappedIntVector(Sequence[int]):
    """Fixed-width unsigned integer vector backed by a temporary mmap."""

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
        self._mmap: mmap.mmap | None = None
        self._closed = False

        _check_unsigned(fill, self._max_value)
        if self._byte_count > 0:
            self._file_handle = tempfile.TemporaryFile(
                dir=None if spool_dir is None else Path(spool_dir)
            )
            self._file_handle.truncate(self._byte_count)
            self._mmap = mmap.mmap(self._file_handle.fileno(), self._byte_count)
            if fill != 0:
                self.fill(fill)

    @property
    def typecode(self) -> str:
        """Return an array-compatible unsigned integer type code."""
        return _typecode_for_width(self._width)

    @property
    def byte_count(self) -> int:
        """Return allocated mmap bytes."""
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
        mm = self._require_mmap()
        return self._format.unpack_from(mm, index * self._width)[0]

    def __setitem__(self, index: int, value: int) -> None:
        self._require_open()
        index = self._normalize_index(index)
        _check_unsigned(value, self._max_value)
        mm = self._require_mmap()
        self._format.pack_into(mm, index * self._width, value)

    def fill(self, value: int) -> None:
        """Set every slot to one unsigned value."""
        self._require_open()
        _check_unsigned(value, self._max_value)
        if self._length == 0:
            return

        mm = self._require_mmap()
        packed = self._format.pack(value)
        repeat = max(1, _FILL_CHUNK_BYTES // self._width)
        chunk = packed * min(self._length, repeat)
        offset = 0
        remaining = self._length

        while remaining:
            count = min(remaining, len(chunk) // self._width)
            byte_count = count * self._width
            mm[offset:offset + byte_count] = chunk[:byte_count]
            offset += byte_count
            remaining -= count

    def close(self) -> None:
        """Close mmap and temporary file resources."""
        if self._closed:
            return

        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._file_handle is not None:
            self._file_handle.close()
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

    def _require_mmap(self) -> mmap.mmap:
        if self._mmap is None:
            raise IndexError("empty vector has no storage")
        return self._mmap

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("mapped integer vector is closed")


class MappedRecordVector(Sequence[tuple[int, ...]]):
    """Fixed-size record vector backed by a temporary mmap."""

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

        if not record_format:
            raise ValueError("record format must not be empty")
        if record_format[0] not in "@=<>!":
            record_format = "<" + record_format

        self._struct = struct.Struct(record_format)
        self._capacity = capacity
        self._length = length
        self._byte_count = capacity * self._struct.size
        self._file_handle: BinaryIO | None = None
        self._mmap: mmap.mmap | None = None
        self._closed = False

        if self._byte_count > 0:
            self._file_handle = tempfile.TemporaryFile(
                dir=None if spool_dir is None else Path(spool_dir)
            )
            self._file_handle.truncate(self._byte_count)
            self._mmap = mmap.mmap(self._file_handle.fileno(), self._byte_count)

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
        """Return allocated mmap bytes."""
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
        mm = self._require_mmap()
        return self._struct.unpack_from(mm, index * self._struct.size)

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
        """Close mmap and temporary file resources."""
        if self._closed:
            return

        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._file_handle is not None:
            self._file_handle.close()
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
        mm = self._require_mmap()
        self._struct.pack_into(mm, index * self._struct.size, *record)

    def _normalize_index(self, index: int) -> int:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return index

    def _require_mmap(self) -> mmap.mmap:
        if self._mmap is None:
            raise IndexError("empty record vector has no storage")
        return self._mmap

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("mapped record vector is closed")


class ChunkedMappedRecordVector(Sequence[tuple[int, ...]]):
    """Append-only record vector that grows by mmap-backed chunks."""

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
        self._length = 0
        self._closed = False

    @property
    def byte_count(self) -> int:
        """Return allocated mmap bytes across chunks."""
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
        chunk_index, record_index = divmod(index, self._chunk_capacity)
        return self._chunks[chunk_index][record_index]

    def append(self, record: Sequence[int]) -> int:
        """Append a record and return its zero-based index."""
        self._require_open()
        if self._length == len(self._chunks) * self._chunk_capacity:
            self._chunks.append(
                MappedRecordVector(
                    self._chunk_capacity,
                    self._record_format,
                    spool_dir=self._spool_dir,
                )
            )

        index = self._length
        self._chunks[-1].append(record)
        self._length += 1
        return index

    def close(self) -> None:
        """Close every allocated chunk."""
        if self._closed:
            return

        for chunk in self._chunks:
            chunk.close()
        self._chunks.clear()
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
    """Track mapped resources and byte high-water use."""

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
        """Track a mapped resource for deterministic cleanup."""
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
