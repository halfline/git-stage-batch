"""Line-addressable byte buffers with optional mmap-backed storage."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import nullcontext
import mmap
from pathlib import Path
from types import TracebackType
from typing import (
    BinaryIO,
    ContextManager,
    Generic,
    Iterator,
    TypeVar,
    overload,
)

from .mapped_storage import (
    ChunkedMappedRecordVector,
    byte_storage_from_chunks,
    byte_storage_from_path,
)
from .resource_cleanup import close_resources_preserving_first
from .text_lines import AcquirableLineSequence


_DEFAULT_CHUNK_SIZE = 1024 * 1024
_LINE_SPAN_CHUNK_CAPACITY = 65536
_BytesLike = bytes | bytearray | memoryview
_LineT = TypeVar("_LineT")


class _BufferBacking:
    """Reference-counted immutable byte storage shared by line buffers."""

    def __init__(
        self,
        data: bytes | mmap.mmap,
        file_handle: BinaryIO | None = None,
    ) -> None:
        self.data = data
        self.file_handle = file_handle
        self._reference_count = 1
        self._closed = False

    def retain(self) -> _BufferBacking:
        if self._closed:
            raise ValueError("buffer backing is closed")
        self._reference_count += 1
        return self

    def release(self) -> None:
        if self._closed:
            return
        if self._reference_count > 1:
            self._reference_count -= 1
            return

        failure: BaseException | None = None
        try:
            if isinstance(self.data, mmap.mmap):
                self.data.close()
        except BaseException as error:
            failure = error
        try:
            if self.file_handle is not None:
                self.file_handle.close()
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure
        self._reference_count = 0
        self._closed = True


class _LineSpanVector:
    """Compact append-only storage for byte line spans."""

    def __init__(self, *, spool_dir: str | Path | None = None) -> None:
        self._records = ChunkedMappedRecordVector(
            record_format="QQ",
            chunk_capacity=_LINE_SPAN_CHUNK_CAPACITY,
            spool_dir=spool_dir,
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


class LineBuffer(Sequence[bytes]):
    """Random-access line buffer with explicit resource cleanup."""

    def __init__(
        self,
        data: bytes | mmap.mmap,
        *,
        file_handle: BinaryIO | None = None,
        spool_dir: str | Path | None = None,
    ) -> None:
        self._backing = _BufferBacking(data, file_handle)
        self._spool_dir = spool_dir
        try:
            self._initialize_line_index()
        except BaseException:
            try:
                self._backing.release()
            except BaseException:
                pass
            raise

    def _initialize_line_index(self) -> None:
        scan_complete = len(self._data) == 0
        line_spans = _LineSpanVector(spool_dir=self._spool_dir)
        self._line_spans = line_spans
        self._line_spans_are_explicit = False
        self._scan_position = 0
        self._scan_complete = scan_complete
        self._line_spans_released = False
        self._backing_released = False
        self._closed = False

    @classmethod
    def _from_backing(
        cls,
        backing: _BufferBacking,
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        buffer = cls.__new__(cls)
        buffer._backing = backing.retain()
        buffer._spool_dir = spool_dir
        try:
            buffer._initialize_line_index()
        except BaseException:
            try:
                buffer._backing.release()
            except BaseException:
                pass
            raise
        return buffer

    @property
    def _data(self) -> bytes | mmap.mmap:
        return self._backing.data

    @classmethod
    def from_bytes(
        cls,
        data: _BytesLike,
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Create a buffer from in-memory bytes."""
        return cls(bytes(data), spool_dir=spool_dir)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Create a buffer from a file using mmap when possible."""
        data, file_handle = byte_storage_from_path(path)
        return cls(
            data,
            file_handle=file_handle,
            spool_dir=spool_dir,
        )

    @classmethod
    def from_chunks(
        cls,
        chunks: Iterable[_BytesLike],
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Create a buffer from generated chunks."""
        data, file_handle = byte_storage_from_chunks(
            chunks,
            spool_dir=spool_dir,
        )
        return cls(
            data,
            file_handle=file_handle,
            spool_dir=spool_dir,
        )

    @classmethod
    def from_line_chunks(
        cls,
        lines: Iterable[_BytesLike],
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Create a buffer whose supplied chunks retain explicit line bounds."""
        line_spans = _LineSpanVector(spool_dir=spool_dir)
        byte_count = 0

        def indexed_chunks() -> Iterator[_BytesLike]:
            nonlocal byte_count
            for line in lines:
                if not isinstance(line, (bytes, bytearray, memoryview)):
                    raise TypeError(
                        f"expected bytes-like object, got {type(line).__name__}"
                    )
                line_size = line.nbytes if isinstance(line, memoryview) else len(line)
                next_byte_count = byte_count + line_size
                line_spans.append(byte_count, next_byte_count)
                byte_count = next_byte_count
                yield line

        try:
            data, file_handle = byte_storage_from_chunks(
                indexed_chunks(),
                spool_dir=spool_dir,
            )
        except BaseException:
            close_resources_preserving_first(
                (line_spans,),
                suppress_errors=True,
            )
            raise

        buffer = cls.__new__(cls)
        buffer._backing = _BufferBacking(data, file_handle)
        buffer._spool_dir = spool_dir
        buffer._line_spans = line_spans
        buffer._line_spans_are_explicit = True
        buffer._scan_position = byte_count
        buffer._scan_complete = True
        buffer._line_spans_released = False
        buffer._backing_released = False
        buffer._closed = False
        return buffer

    def clone(
        self,
        *,
        spool_dir: str | Path | None = None,
    ) -> LineBuffer:
        """Return an independently closable buffer sharing immutable storage."""
        self._require_open()
        clone_spool_dir = self._spool_dir if spool_dir is None else spool_dir
        if self._line_spans_are_explicit:
            line_spans = _LineSpanVector(spool_dir=clone_spool_dir)
            try:
                for index in range(self._line_span_count()):
                    line_spans.append(*self._get_line_span(index))
                backing = self._backing.retain()
            except BaseException:
                close_resources_preserving_first(
                    (line_spans,),
                    suppress_errors=True,
                )
                raise

            buffer = LineBuffer.__new__(LineBuffer)
            buffer._backing = backing
            buffer._spool_dir = clone_spool_dir
            buffer._line_spans = line_spans
            buffer._line_spans_are_explicit = True
            buffer._scan_position = self._scan_position
            buffer._scan_complete = True
            buffer._line_spans_released = False
            buffer._backing_released = False
            buffer._closed = False
            return buffer

        return LineBuffer._from_backing(
            self._backing,
            spool_dir=clone_spool_dir,
        )

    @property
    def byte_count(self) -> int:
        """Return the number of bytes in the buffer."""
        self._require_open()
        return len(self._data)

    def close(self) -> None:
        """Close any open mmap and file resources."""
        if self._line_spans_released and self._backing_released:
            return
        self._closed = True
        failure: BaseException | None = None
        if not self._line_spans_released:
            try:
                self._line_spans.close()
            except BaseException as error:
                failure = error
            else:
                self._line_spans_released = True
        if not self._backing_released:
            try:
                self._backing.release()
            except BaseException as error:
                if failure is None:
                    failure = error
            else:
                self._backing_released = True
        if failure is not None:
            raise failure

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
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
            yield self._data[start : start + chunk_size]

    def __enter__(self) -> LineBuffer:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        close_resources_preserving_first(
            (self,),
            suppress_errors=exc_type is not None,
        )

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
    """Scoped no-copy view over one line buffer line."""

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
            if isinstance(index, slice):
                result = view[index]
                try:
                    return bytes(result)
                finally:
                    result.release()
            return view[index]
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
            return "<LineBufferLineView closed>"
        return f"<LineBufferLineView {bytes(self)!r}>"

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
        tail = view[len(view) - len(suffix_bytes) :]
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
            return base[self._start : self._end]
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

    def __init__(self, buffer: LineBuffer) -> None:
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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


class _BufferLineSliceSequence(Sequence[_LineT], Generic[_LineT]):
    """Lazy slice view over line buffer lines."""

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

    def acquire_lines(self) -> ContextManager[Sequence[_LineT]]:
        """Return a context manager for acquired lines from this slice."""
        if isinstance(self._parent, AcquirableLineSequence):
            return _AcquiredBufferLineSliceContext(
                self._parent.acquire_lines(),
                self._slice,
            )
        return nullcontext(self)

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


class _AcquiredBufferLineSliceContext(Generic[_LineT]):
    """Context manager for acquired line views from a slice sequence."""

    def __init__(
        self,
        parent_context: ContextManager[Sequence[_LineT]],
        line_slice: slice,
    ) -> None:
        self._parent_context = parent_context
        self._slice = line_slice
        self._lines: Sequence[_LineT] | None = None

    def __enter__(self) -> Sequence[_LineT]:
        parent = self._parent_context.__enter__()
        self._lines = _BufferLineSliceSequence(parent, self._slice)
        return self._lines

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._lines = None
        return self._parent_context.__exit__(exc_type, exc, traceback)


def _slice_uses_negative_bounds(line_slice: slice) -> bool:
    return (line_slice.start is not None and line_slice.start < 0) or (
        line_slice.stop is not None and line_slice.stop < 0
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
    if isinstance(buffer, LineBuffer):
        yield from buffer.byte_chunks(chunk_size)
        return
    if isinstance(buffer, (bytes, bytearray, memoryview)):
        yield bytes(buffer)
        return

    for chunk in buffer:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(f"expected bytes-like object, got {type(chunk).__name__}")
        yield bytes(chunk)


def buffer_ends_with_lf(buffer: BufferInput) -> bool:
    """Return whether a buffer input ends with a newline byte."""
    last_chunk = b""
    for chunk in buffer_byte_chunks(buffer):
        if chunk:
            last_chunk = chunk
    return bool(last_chunk) and last_chunk.endswith(b"\n")


def buffer_matches(left: BufferInput, right: BufferInput) -> bool:
    """Return whether two buffer inputs contain the same bytes."""
    left_count = _known_byte_count(left)
    right_count = _known_byte_count(right)
    if left_count is not None and right_count is not None and left_count != right_count:
        return False

    return _buffer_chunks_match(
        buffer_byte_chunks(left),
        buffer_byte_chunks(right),
    )


def _known_byte_count(buffer: BufferInput) -> int | None:
    if isinstance(buffer, LineBuffer):
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
