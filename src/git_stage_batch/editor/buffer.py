"""Editor buffers with random access over bytes or mmap-backed files."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import nullcontext
import mmap
import os
from pathlib import Path
from typing import Any, BinaryIO, Generic, Iterator, TypeVar, overload

from ..utils.mapped_storage import (
    ChunkedMappedRecordVector,
    byte_storage_from_chunks,
    byte_storage_from_path,
)


_DEFAULT_CHUNK_SIZE = 1024 * 1024
_LINE_SPAN_CHUNK_CAPACITY = 65536
_BytesLike = bytes | bytearray | memoryview
_LineT = TypeVar("_LineT")


class _LineSpanVector:
    """Compact append-only storage for byte line spans."""

    def __init__(self) -> None:
        self._records = ChunkedMappedRecordVector(
            record_format="QQ",
            chunk_capacity=_LINE_SPAN_CHUNK_CAPACITY,
        )

    def __len__(self) -> int:
        return len(self._records)

    def append(self, start: int, end: int) -> None:
        self._records.append((start, end))

    def get(self, index: int) -> tuple[int, int]:
        start, end = self._records[index]
        return start, end

    def close(self) -> None:
        self._records.close()


class EditorBuffer(Sequence[bytes]):
    """Random-access editor buffer with explicit resource cleanup."""

    def __init__(
        self,
        data: bytes | mmap.mmap,
        *,
        file_handle: BinaryIO | None = None,
    ) -> None:
        self._data = data
        self._file_handle = file_handle
        self._line_spans = _LineSpanVector()
        self._scan_position = 0
        self._scan_complete = len(data) == 0
        self._closed = False

    @classmethod
    def from_bytes(cls, data: _BytesLike) -> EditorBuffer:
        """Create a buffer from in-memory bytes."""
        return cls(bytes(data))

    @classmethod
    def from_path(cls, path: str | Path) -> EditorBuffer:
        """Create a buffer from a file using mmap when possible."""
        data, file_handle = byte_storage_from_path(path)
        return cls(data, file_handle=file_handle)

    @classmethod
    def from_chunks(
        cls,
        chunks: Iterable[_BytesLike],
        *,
        spool_dir: str | Path | None = None,
    ) -> EditorBuffer:
        """Create a buffer from generated chunks."""
        data, file_handle = byte_storage_from_chunks(
            chunks,
            spool_dir=spool_dir,
        )
        return cls(data, file_handle=file_handle)

    @property
    def uses_mapped_storage(self) -> bool:
        """Return whether this buffer uses mapped storage."""
        return isinstance(self._data, mmap.mmap) and not self._closed

    @property
    def byte_count(self) -> int:
        """Return the number of bytes in the buffer."""
        self._require_open()
        return len(self._data)

    def close(self) -> None:
        """Close any open mmap and file resources."""
        if self._closed:
            return

        data = self._data
        if isinstance(data, mmap.mmap):
            data.close()
        if self._file_handle is not None:
            self._file_handle.close()
        self._line_spans.close()
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def to_bytes(self) -> bytes:
        """Materialize the full buffer as a bytes object."""
        self._require_open()
        if isinstance(self._data, bytes):
            return self._data
        return self._data[:]

    def byte_chunks(self, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        """Yield the buffer as byte chunks."""
        self._require_open()
        if chunk_size <= 0:
            raise ValueError("chunk size must be positive")

        for start in range(0, len(self._data), chunk_size):
            yield self._data[start:start + chunk_size]

    def __enter__(self) -> EditorBuffer:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def acquire_line(self, index: int) -> _AcquiredBufferLineContext:
        """Return a context manager for a scoped no-copy line view."""
        return _AcquiredBufferLineContext(self, index)

    def acquire_lines(self) -> _AcquiredBufferLineSequence:
        """Return a context manager for scoped no-copy line views."""
        return _AcquiredBufferLineSequence(self)

    def __len__(self) -> int:
        self._scan_all_lines()
        return self._line_span_count()

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        self._require_open()
        if isinstance(index, slice):
            return _BufferLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)

        self._scan_through_line(index)
        if index >= self._line_span_count():
            raise IndexError(index)

        start, end = self._get_line_span(index)
        return self._data[start:end]

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("buffer is closed")

    def _scan_all_lines(self) -> None:
        self._require_open()
        while not self._scan_complete:
            self._scan_next_line()

    def _scan_through_line(self, index: int) -> None:
        self._require_open()
        while self._line_span_count() <= index and not self._scan_complete:
            self._scan_next_line()

    def _line_span_count(self) -> int:
        return len(self._line_spans)

    def _append_line_span(self, start: int, end: int) -> None:
        self._line_spans.append(start, end)

    def _get_line_span(self, index: int) -> tuple[int, int]:
        return self._line_spans.get(index)

    def _scan_next_line(self) -> None:
        data = self._data
        content_length = len(data)
        start = self._scan_position

        if start >= content_length:
            self._scan_complete = True
            return

        next_lf = data.find(b"\n", start)

        if next_lf == -1:
            self._append_line_span(start, content_length)
            self._scan_position = content_length
            self._scan_complete = True
            return

        end = next_lf + 1
        self._append_line_span(start, end)
        self._scan_position = end
        if self._scan_position >= content_length:
            self._scan_complete = True


class _BufferLineView:
    """Scoped no-copy view over one editor buffer line."""

    __slots__ = ("_owner", "_start", "_end", "_hash")

    def __init__(
        self,
        owner: _AcquiredBufferLineSequence,
        start: int,
        end: int,
    ) -> None:
        self._owner = owner
        self._start = start
        self._end = end
        self._hash: int | None = None

    def __bytes__(self) -> bytes:
        view = self._memoryview()
        try:
            return bytes(view)
        finally:
            view.release()

    def __len__(self) -> int:
        self._require_active()
        return self._end - self._start

    @overload
    def __getitem__(self, index: int) -> int: ...

    @overload
    def __getitem__(self, index: slice) -> bytes: ...

    def __getitem__(self, index: int | slice) -> int | bytes:
        view = self._memoryview()
        try:
            result = view[index]
            if isinstance(index, slice):
                try:
                    return bytes(result)
                finally:
                    result.release()
            return result
        finally:
            view.release()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _BufferLineView):
            return self._equals_line_view(other)

        if isinstance(other, (bytes, bytearray, memoryview)):
            view = self._memoryview()
            try:
                return view == other
            finally:
                view.release()

        return NotImplemented

    def __hash__(self) -> int:
        self._require_active()
        if self._hash is not None:
            return self._hash

        view = self._memoryview()
        try:
            self._hash = hash(view)
            return self._hash
        finally:
            view.release()

    def __repr__(self) -> str:
        if not self._owner.is_active:
            return "<EditorBufferLineView closed>"
        return f"<EditorBufferLineView {bytes(self)!r}>"

    def endswith(self, suffix: _BytesLike | tuple[_BytesLike, ...]) -> bool:
        """Return whether the line ends with the given bytes-like suffix."""
        if isinstance(suffix, tuple):
            return any(self.endswith(item) for item in suffix)
        if not isinstance(suffix, (bytes, bytearray, memoryview)):
            raise TypeError("suffix must be bytes-like")

        suffix_bytes = bytes(suffix)
        if suffix_bytes == b"":
            self._require_active()
            return True
        if len(suffix_bytes) > len(self):
            return False

        view = self._memoryview()
        tail = view[len(view) - len(suffix_bytes):]
        try:
            return tail == suffix_bytes
        finally:
            tail.release()
            view.release()

    def _require_active(self) -> None:
        self._owner._require_active()

    def _memoryview(self) -> memoryview:
        self._require_active()
        base = memoryview(self._owner.data)
        try:
            return base[self._start:self._end]
        finally:
            base.release()

    def _equals_line_view(self, other: _BufferLineView) -> bool:
        left = self._memoryview()
        try:
            right = other._memoryview()
            try:
                return left == right
            finally:
                right.release()
        finally:
            left.release()


class _AcquiredBufferLineSequence(Sequence[_BufferLineView]):
    """Context-managed sequence of scoped no-copy editor line views."""

    def __init__(self, buffer: EditorBuffer) -> None:
        self._buffer = buffer
        self._active = False

    @property
    def data(self) -> bytes | mmap.mmap:
        self._require_active()
        return self._buffer._data

    @property
    def is_active(self) -> bool:
        return self._active and not self._buffer._closed

    def __enter__(self) -> _AcquiredBufferLineSequence:
        self._buffer._require_open()
        self._active = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._active = False

    def __len__(self) -> int:
        self._require_active()
        return len(self._buffer)

    @overload
    def __getitem__(self, index: int) -> _BufferLineView: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_BufferLineView]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> _BufferLineView | Sequence[_BufferLineView]:
        self._require_active()
        if isinstance(index, slice):
            return _BufferLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)

        self._buffer._scan_through_line(index)
        if index >= self._buffer._line_span_count():
            raise IndexError(index)

        start, end = self._buffer._get_line_span(index)
        return _BufferLineView(self, start, end)

    def _require_active(self) -> None:
        if not self._active:
            raise ValueError("line view is closed")
        self._buffer._require_open()


class _AcquiredBufferLineContext:
    """Context manager for a single scoped editor line view."""

    def __init__(self, buffer: EditorBuffer, index: int) -> None:
        self._lines_context = _AcquiredBufferLineSequence(buffer)
        self._index = index

    def __enter__(self) -> _BufferLineView:
        lines = self._lines_context.__enter__()
        return lines[self._index]

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._lines_context.__exit__(exc_type, exc, traceback)


class _BufferLineSliceSequence(Sequence[_LineT], Generic[_LineT]):
    """Lazy slice view over editor buffer lines."""

    def __init__(
        self,
        parent: Sequence[_LineT],
        line_slice: slice,
    ) -> None:
        if line_slice.step == 0:
            raise ValueError("slice step cannot be zero")
        self._parent = parent
        self._slice = line_slice

    def __len__(self) -> int:
        return len(range(*self._resolved_range()))

    def acquire_lines(self) -> Any:
        """Return a context manager for acquired lines from this slice."""
        acquire_lines = getattr(self._parent, "acquire_lines", None)
        if acquire_lines is None:
            return nullcontext(self)
        return _AcquiredBufferLineSliceContext(acquire_lines(), self._slice)

    @overload
    def __getitem__(self, index: int) -> _LineT: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_LineT]: ...

    def __getitem__(self, index: int | slice) -> _LineT | Sequence[_LineT]:
        if isinstance(index, slice):
            return _BufferLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)

        parent_index = self._parent_index(index)
        if parent_index is None:
            raise IndexError(index)

        try:
            return self._parent[parent_index]
        except IndexError as exc:
            raise IndexError(index) from exc

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented

        if len(self) != len(other):
            return False

        return all(self[index] == other[index] for index in range(len(self)))

    def _parent_index(self, index: int) -> int | None:
        line_slice = self._slice
        step = 1 if line_slice.step is None else line_slice.step
        if step < 0 or _slice_uses_negative_bounds(line_slice):
            line_range = range(*self._resolved_range())
            try:
                return line_range[index]
            except IndexError:
                return None

        start = 0 if line_slice.start is None else line_slice.start
        parent_index = start + index * step

        if line_slice.stop is not None and parent_index >= line_slice.stop:
            return None

        return parent_index

    def _resolved_range(self) -> tuple[int, int, int]:
        return self._slice.indices(len(self._parent))


class _AcquiredBufferLineSliceContext:
    """Context manager for acquired line views from a slice sequence."""

    def __init__(self, parent_context: Any, line_slice: slice) -> None:
        self._parent_context = parent_context
        self._slice = line_slice
        self._lines: Sequence[Any] | None = None

    def __enter__(self) -> Sequence[Any]:
        parent = self._parent_context.__enter__()
        self._lines = _BufferLineSliceSequence(parent, self._slice)
        return self._lines

    def __exit__(self, exc_type, exc, traceback) -> Any:
        self._lines = None
        return self._parent_context.__exit__(exc_type, exc, traceback)


def _slice_uses_negative_bounds(line_slice: slice) -> bool:
    return (
        (line_slice.start is not None and line_slice.start < 0)
        or (line_slice.stop is not None and line_slice.stop < 0)
    )


def buffer_has_data(buffer: Sequence[bytes]) -> bool:
    """Return whether a buffer has any non-empty line entry."""
    return any(line for line in buffer)


BufferInput = _BytesLike | Sequence[bytes]


def buffer_byte_chunks(
    buffer: BufferInput,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> Iterator[bytes]:
    """Yield bytes for in-memory, line-sequence, or buffer input."""
    if isinstance(buffer, EditorBuffer):
        yield from buffer.byte_chunks(chunk_size)
        return
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        yield bytes(buffer)
        return

    for chunk in buffer:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"expected bytes-like object, got {type(chunk).__name__}"
            )
        yield bytes(chunk)


def buffer_matches(left: BufferInput, right: BufferInput) -> bool:
    """Return whether two buffer inputs contain the same bytes."""
    left_count = _known_byte_count(left)
    right_count = _known_byte_count(right)
    if (
        left_count is not None
        and right_count is not None
        and left_count != right_count
    ):
        return False

    return _buffer_chunks_match(
        buffer_byte_chunks(left),
        buffer_byte_chunks(right),
    )


def _known_byte_count(buffer: BufferInput) -> int | None:
    if isinstance(buffer, EditorBuffer):
        return buffer.byte_count
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        return len(buffer)
    return None


def _buffer_chunks_match(
    left_chunks: Iterable[bytes],
    right_chunks: Iterable[bytes],
) -> bool:
    left_iter = iter(left_chunks)
    right_iter = iter(right_chunks)
    left_chunk = b""
    right_chunk = b""
    left_done = False
    right_done = False

    while True:
        while left_chunk == b"" and not left_done:
            try:
                left_chunk = next(left_iter)
            except StopIteration:
                left_done = True

        while right_chunk == b"" and not right_done:
            try:
                right_chunk = next(right_iter)
            except StopIteration:
                right_done = True

        if left_done or right_done:
            return left_done and right_done

        compare_size = min(len(left_chunk), len(right_chunk))
        if left_chunk[:compare_size] != right_chunk[:compare_size]:
            return False

        left_chunk = left_chunk[compare_size:]
        right_chunk = right_chunk[compare_size:]


def buffer_byte_count(buffer: BufferInput) -> int:
    """Return the number of bytes in a buffer input."""
    known_count = _known_byte_count(buffer)
    if known_count is not None:
        return known_count
    return sum(len(chunk) for chunk in buffer_byte_chunks(buffer))


def buffer_preview(buffer: BufferInput, size: int = 200) -> bytes:
    """Return up to size bytes from the front of a buffer input."""
    if size < 0:
        raise ValueError("preview size must be non-negative")

    preview = bytearray()
    for chunk in buffer_byte_chunks(buffer):
        remaining = size - len(preview)
        if remaining <= 0:
            break
        preview.extend(chunk[:remaining])
        if len(preview) >= size:
            break
    return bytes(preview)


def write_buffer_to_path(path: str | Path, buffer: BufferInput) -> None:
    """Write buffer bytes to a path, creating parent directories as needed."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if file_path.is_symlink():
        target = b"".join(buffer_byte_chunks(buffer))
        file_path.unlink()
        os.symlink(target, os.fsencode(file_path))
        return

    with file_path.open("wb") as file_handle:
        for chunk in buffer_byte_chunks(buffer):
            file_handle.write(chunk)


def write_buffer_to_working_tree_path(
    path: str | Path,
    buffer: BufferInput,
    *,
    mode: str | None = None,
) -> None:
    """Write buffer bytes as a Git working-tree path with the given mode."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "120000":
        target = b"".join(buffer_byte_chunks(buffer))
        if os.path.lexists(file_path):
            file_path.unlink()
        os.symlink(target, os.fsencode(file_path))
        return

    if file_path.is_symlink():
        file_path.unlink()

    with file_path.open("wb") as file_handle:
        for chunk in buffer_byte_chunks(buffer):
            file_handle.write(chunk)

    if mode == "100755":
        current_mode = file_path.stat().st_mode
        file_path.chmod(current_mode | 0o111)
    elif mode == "100644":
        current_mode = file_path.stat().st_mode
        file_path.chmod(current_mode & ~0o111)
