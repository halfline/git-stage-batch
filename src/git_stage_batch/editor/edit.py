"""Editor mutation helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from .buffer import EditorBuffer
from .line_endings import detect_line_ending, restore_line_endings_in_chunks
from ..utils.text import normalize_line_ending


class Cursor:
    """An opaque position in editor lines."""

    __slots__ = ("_editor", "_id")

    def __init__(self, editor: Editor, cursor_id: int) -> None:
        self._editor = editor
        self._id = cursor_id


@dataclass(slots=True)
class _SourceLineSegment:
    start: int
    end: int | None


@dataclass(slots=True)
class _BufferLineSegment:
    buffer: EditorBuffer
    start: int
    end: int


_LineSegment = _SourceLineSegment | _BufferLineSegment
_BytesLike = bytes | bytearray | memoryview
_TransformResult = _BytesLike | Iterable[bytes]
_Selection = tuple[int, int | None]


class Editor:
    """Stateful line editor for indexed lines."""

    def __init__(self, source: Sequence[bytes]) -> None:
        self._source = source
        self._segments: list[_LineSegment] = [
            _SourceLineSegment(0, None),
        ]
        self._line_count: int | None = None
        self._position = 0
        self._selection: _Selection | None = None
        self._cursor_positions: dict[int, int] = {}
        self._next_cursor_id = 0
        self._owned_buffers: list[EditorBuffer] = []
        self._closed = False

    def __enter__(self) -> Editor:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def position(self) -> int:
        """Return the current 0-based destination line boundary."""
        self._require_open()
        return self._position

    @property
    def at_end(self) -> bool:
        """Return whether the editor is positioned at end of its lines."""
        self._require_open()
        return self._position == self._current_line_count()

    def cursor_at(self, line: int) -> Cursor:
        """Return a cursor at a 0-based destination line boundary."""
        self._require_open()
        position = self._validated_position(line)
        cursor_id = self._next_cursor_id
        self._next_cursor_id += 1
        self._cursor_positions[cursor_id] = position
        return Cursor(self, cursor_id)

    def cursor_at_source_line(self, line: int) -> Cursor:
        """Return a cursor at a 0-based source line boundary."""
        self._require_open()
        if line < 0:
            raise ValueError("source line is out of range")

        destination_position = self._destination_position_for_source_line(line)
        if destination_position is None:
            raise ValueError("source line is not present in edited lines")

        return self.cursor_at(destination_position)

    def move_to(self, target: Cursor | int) -> None:
        """Move to a cursor or destination line boundary."""
        self._require_open()
        self._selection = None
        self._position = self._resolve_position(target)

    def select_lines(self, count: int) -> None:
        """Select count lines from the current position."""
        self._require_open()
        if count < 0:
            raise ValueError("line count must be non-negative")

        selection_end = self._position + count
        self._validated_position(selection_end)

        self._selection = (self._position, selection_end)

    def select_to(self, target: Cursor | int) -> None:
        """Select lines from the current position to the target."""
        self._require_open()
        self._selection = (self._position, self._resolve_position(target))

    def select_all(self) -> None:
        """Select all lines from the start."""
        self._require_open()
        self._selection = (0, None)

    def add_line(self, payload: bytes) -> None:
        """Insert or replace with one line."""
        self.add_lines((payload,))

    def add_lines(self, payloads: Iterable[bytes]) -> None:
        """Insert or replace with generated line payloads."""
        self._require_open()
        inserted_buffer = _spool_inserted_lines(payloads)
        self._owned_buffers.append(inserted_buffer.buffer)
        self._commit_edit(inserted_buffer)

    def add_bytes(self, data: bytes) -> None:
        """Insert raw bytes split on line endings."""
        self._require_open()
        if not isinstance(data, bytes):
            raise TypeError(f"expected bytes object, got {type(data).__name__}")

        buffer = EditorBuffer.from_bytes(data)
        self._owned_buffers.append(buffer)
        self._commit_edit(
            _InsertedBuffer(
                buffer=buffer,
                line_count=_count_lines_in_bytes(data),
            )
        )

    def remove(self) -> None:
        """Remove the pending selection."""
        self._require_open()
        if self._selection is None:
            raise ValueError("no line selection")

        self._commit_edit(None)

    def transform(
        self,
        handler: Callable[[Sequence[bytes]], _TransformResult],
    ) -> None:
        """Replace the current selection with transformed lines."""
        self._require_open()
        selection_start, selection_end = self._selected_range()
        result = handler(
            _SelectedLineSequence(
                self,
                selection_start,
                selection_end,
            )
        )

        if isinstance(result, (bytes, bytearray, memoryview)):
            self.add_bytes(bytes(result))
        else:
            self.add_lines(result)

    def export(
        self,
        *,
        has_trailing_newline: bool = True,
        add_trailing_newline_when_nonempty: bool = False,
        line_endings_from: Sequence[bytes] | None = None,
    ) -> EditorBuffer:
        """Materialize final buffer and freeze the editor."""
        self._require_open()
        try:
            chunks = _line_body_chunks(
                self._line_bodies(),
                has_trailing_newline=has_trailing_newline,
                add_trailing_newline_when_nonempty=(
                    add_trailing_newline_when_nonempty
                ),
            )
            if line_endings_from is not None:
                chunks = restore_line_endings_in_chunks(
                    chunks,
                    detect_line_ending(line_endings_from),
                )
            return EditorBuffer.from_chunks(
                chunks
            )
        finally:
            self.close()

    def close(self) -> None:
        """Close generated buffers held by the editor."""
        if self._closed:
            return

        for buffer in self._owned_buffers:
            buffer.close()
        self._closed = True

    def _commit_edit(self, inserted_buffer: _InsertedBuffer | None) -> None:
        selection_start, selection_end = self._selected_range()
        inserted_line_count = (
            inserted_buffer.line_count
            if inserted_buffer is not None
            else 0
        )
        inserted_segment = (
            _BufferLineSegment(
                inserted_buffer.buffer,
                0,
                inserted_buffer.line_count,
            )
            if inserted_buffer is not None and inserted_buffer.line_count > 0
            else None
        )

        self._replace_selected_range(
            selection_start,
            selection_end,
            inserted_segment,
        )
        self._shift_cursors(
            selection_start,
            selection_end,
            inserted_line_count,
        )
        if selection_end is None and selection_start == 0:
            self._line_count = inserted_line_count
        elif self._line_count is not None:
            resolved_selection_end = self._resolve_selection_end(selection_end)
            self._line_count += inserted_line_count - (
                resolved_selection_end - selection_start
            )
        self._position = selection_start + inserted_line_count
        self._selection = None

    def _selected_range(self) -> _Selection:
        if self._selection is None:
            return self._position, self._position

        selection_start, selection_end = self._selection
        if selection_end is None:
            return selection_start, None
        return min(selection_start, selection_end), max(selection_start, selection_end)

    def _replace_selected_range(
        self,
        selection_start: int,
        selection_end: int | None,
        inserted_segment: _LineSegment | None,
    ) -> None:
        replacement_segments: list[_LineSegment] = []
        inserted = False
        destination_position = 0

        for segment in self._segments:
            segment_start = destination_position

            if isinstance(segment, _SourceLineSegment) and segment.end is None:
                if selection_end is not None and selection_end <= segment_start:
                    if not inserted:
                        _append_segment(replacement_segments, inserted_segment)
                        inserted = True
                    replacement_segments.append(segment)
                    continue

                prefix_end = max(selection_start - segment_start, 0)
                if prefix_end > 0:
                    replacement_segments.append(
                        _slice_segment(segment, 0, prefix_end)
                    )

                if not inserted:
                    _append_segment(replacement_segments, inserted_segment)
                    inserted = True

                if selection_end is not None:
                    suffix_start = max(selection_end - segment_start, 0)
                    replacement_segments.append(
                        _slice_segment(segment, suffix_start, None)
                    )
                continue

            segment_line_count = _known_segment_line_count(segment)
            segment_end = segment_start + segment_line_count
            destination_position = segment_end

            if selection_end is not None and segment_end <= selection_start:
                replacement_segments.append(segment)
                continue

            if selection_end is not None and segment_start >= selection_end:
                if not inserted:
                    _append_segment(replacement_segments, inserted_segment)
                    inserted = True
                replacement_segments.append(segment)
                continue

            prefix_end = max(selection_start - segment_start, 0)
            if prefix_end > 0:
                replacement_segments.append(
                    _slice_segment(segment, 0, prefix_end)
                )

            if not inserted:
                _append_segment(replacement_segments, inserted_segment)
                inserted = True

            if selection_end is not None:
                suffix_start = min(selection_end - segment_start, segment_line_count)
                if suffix_start < segment_line_count:
                    replacement_segments.append(
                        _slice_segment(segment, suffix_start, segment_line_count)
                    )

        if not inserted:
            _append_segment(replacement_segments, inserted_segment)

        self._segments = replacement_segments

    def _shift_cursors(
        self,
        selection_start: int,
        selection_end: int | None,
        inserted_line_count: int,
    ) -> None:
        if selection_end is None:
            for cursor_id, position in self._cursor_positions.items():
                if position >= selection_start:
                    self._cursor_positions[cursor_id] = (
                        selection_start + inserted_line_count
                    )
            return

        line_delta = inserted_line_count - (selection_end - selection_start)

        for cursor_id, position in self._cursor_positions.items():
            if position < selection_start:
                continue
            if position <= selection_end:
                self._cursor_positions[cursor_id] = (
                    selection_start + inserted_line_count
                )
            else:
                self._cursor_positions[cursor_id] = position + line_delta

    def _line_bodies(self) -> Iterator[bytes]:
        for segment in self._segments:
            if isinstance(segment, _SourceLineSegment):
                if segment.end is None:
                    index = segment.start
                    while True:
                        try:
                            line = self._source[index]
                        except IndexError:
                            break
                        yield _line_body(line)
                        index += 1
                else:
                    for index in range(segment.start, segment.end):
                        yield _line_body(self._source[index])
            else:
                for index in range(segment.start, segment.end):
                    yield _line_body(segment.buffer[index])

    def _line_at_position(self, position: int) -> bytes:
        destination_position = 0

        for segment in self._segments:
            segment_start = destination_position
            if isinstance(segment, _SourceLineSegment) and segment.end is None:
                if position >= segment_start:
                    return self._source[segment.start + (position - segment_start)]
                raise IndexError(position)

            segment_line_count = _known_segment_line_count(segment)
            segment_end = segment_start + segment_line_count
            if segment_start <= position < segment_end:
                segment_index = segment.start + (position - segment_start)
                if isinstance(segment, _SourceLineSegment):
                    return self._source[segment_index]
                return segment.buffer[segment_index]
            destination_position = segment_end

        raise IndexError(position)

    def _destination_position_for_source_line(self, line: int) -> int | None:
        destination_position = 0

        for segment in self._segments:
            if isinstance(segment, _SourceLineSegment) and segment.end is None:
                if line >= segment.start and self._source_boundary_exists(line):
                    return destination_position + (line - segment.start)
                return None

            segment_line_count = _known_segment_line_count(segment)
            if (
                isinstance(segment, _SourceLineSegment)
                and segment.start <= line <= segment.end
            ):
                return destination_position + (line - segment.start)
            destination_position += segment_line_count

        return None

    def _resolve_position(self, target: Cursor | int) -> int:
        if isinstance(target, Cursor):
            if target._editor is not self:
                raise ValueError("cursor does not belong to this editor")
            try:
                return self._cursor_positions[target._id]
            except KeyError as exc:
                raise ValueError("cursor is no longer valid") from exc

        return self._validated_position(target)

    def _validated_position(self, line: int) -> int:
        if line < 0:
            raise ValueError("destination line is out of range")
        if self._line_count is not None:
            if line > self._line_count:
                raise ValueError("destination line is out of range")
            return line

        destination_position = 0
        for segment in self._segments:
            if isinstance(segment, _SourceLineSegment) and segment.end is None:
                source_line = segment.start + (line - destination_position)
                if line >= destination_position and self._source_boundary_exists(
                    source_line
                ):
                    return line
                raise ValueError("destination line is out of range")

            segment_line_count = _known_segment_line_count(segment)
            if line <= destination_position + segment_line_count:
                return line
            destination_position += segment_line_count

        raise ValueError("destination line is out of range")

    def _resolve_selection_end(self, selection_end: int | None) -> int:
        if selection_end is not None:
            return selection_end
        return self._current_line_count()

    def _source_boundary_exists(self, line: int) -> bool:
        if line < 0:
            return False
        if line == 0:
            return True

        try:
            self._source[line - 1]
        except IndexError:
            return False
        return True

    def _current_line_count(self) -> int:
        if self._line_count is not None:
            return self._line_count

        line_count = 0
        for segment in self._segments:
            if isinstance(segment, _SourceLineSegment) and segment.end is None:
                source_line_count = len(self._source)
                segment.end = source_line_count
                line_count += source_line_count - segment.start
            else:
                line_count += _known_segment_line_count(segment)

        self._line_count = line_count
        return line_count

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("editor is closed")


@dataclass(slots=True)
class _InsertedBuffer:
    buffer: EditorBuffer
    line_count: int


class _SelectedLineSequence(Sequence[bytes]):
    """Selected editor lines passed to transform handlers."""

    def __init__(
        self,
        editor: Editor,
        selection_start: int,
        selection_end: int | None,
    ) -> None:
        self._editor = editor
        self._selection_start = selection_start
        self._selection_end = selection_end

    def __len__(self) -> int:
        selection_end = self._editor._resolve_selection_end(self._selection_end)
        return selection_end - self._selection_start

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _SelectedLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)
        if self._selection_end is not None and index >= len(self):
            raise IndexError(index)

        try:
            return normalize_line_ending(
                self._editor._line_at_position(self._selection_start + index)
            )
        except IndexError as exc:
            raise IndexError(index) from exc


class _SelectedLineSliceSequence(Sequence[bytes]):
    """Lazy slice view over selected editor lines."""

    def __init__(
        self,
        parent: Sequence[bytes],
        line_slice: slice,
    ) -> None:
        if line_slice.step == 0:
            raise ValueError("slice step cannot be zero")
        self._parent = parent
        self._slice = line_slice

    def __len__(self) -> int:
        return len(range(*self._resolved_range()))

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _SelectedLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)

        parent_index = self._parent_index(index)
        if parent_index is None:
            raise IndexError(index)

        return self._parent[parent_index]

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


def edit_lines_as_buffer(
    source_lines: Sequence[bytes],
    edited_lines: Iterable[bytes],
    *,
    selection_start: int,
    selection_end: int,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> EditorBuffer:
    """Apply edited lines to an indexed selection and return a buffer."""
    if selection_start < 0 or selection_end < selection_start:
        raise ValueError("invalid line selection")

    try:
        with Editor(source_lines) as editor:
            editor.move_to(selection_start)
            editor.select_to(selection_end)
            editor.add_lines(edited_lines)
            return editor.export(
                has_trailing_newline=has_trailing_newline,
                add_trailing_newline_when_nonempty=(
                    add_trailing_newline_when_nonempty
                ),
            )
    except ValueError as exc:
        if str(exc) in {
            "destination line is out of range",
            "line selection is out of range",
        }:
            raise ValueError("invalid line selection") from exc
        raise


def _spool_inserted_lines(lines: Iterable[bytes]) -> _InsertedBuffer:
    line_count = 0

    def chunks() -> Iterator[bytes]:
        nonlocal line_count
        for line in lines:
            line_count += 1
            yield _line_body(line) + b"\n"

    buffer = EditorBuffer.from_chunks(chunks())
    return _InsertedBuffer(
        buffer=buffer,
        line_count=line_count,
    )


def _count_lines_in_bytes(data: bytes) -> int:
    if not data:
        return 0

    line_count = 0
    line_start = 0
    for index, byte in enumerate(data):
        if byte == 10:
            line_count += 1
            line_start = index + 1

    if line_start < len(data):
        line_count += 1

    return line_count


def _slice_uses_negative_bounds(line_slice: slice) -> bool:
    return (
        (line_slice.start is not None and line_slice.start < 0)
        or (line_slice.stop is not None and line_slice.stop < 0)
    )


def _known_segment_line_count(segment: _LineSegment) -> int:
    if isinstance(segment, _SourceLineSegment) and segment.end is None:
        raise ValueError("source segment has unknown line count")
    return segment.end - segment.start


def _append_segment(
    segments: list[_LineSegment],
    segment: _LineSegment | None,
) -> None:
    if segment is not None and _known_segment_line_count(segment) > 0:
        segments.append(segment)


def _slice_segment(
    segment: _LineSegment,
    start_offset: int,
    end_offset: int | None,
) -> _LineSegment:
    if isinstance(segment, _SourceLineSegment):
        return _SourceLineSegment(
            segment.start + start_offset,
            None if end_offset is None else segment.start + end_offset,
        )

    if end_offset is None:
        raise ValueError("buffer segment slice end is required")

    return _BufferLineSegment(
        segment.buffer,
        segment.start + start_offset,
        segment.start + end_offset,
    )


def _line_body(line: bytes) -> bytes:
    if not isinstance(line, bytes):
        raise TypeError(f"expected bytes object, got {type(line).__name__}")
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _line_body_chunks(
    lines: Iterable[bytes],
    *,
    has_trailing_newline: bool,
    add_trailing_newline_when_nonempty: bool = False,
) -> Iterator[bytes]:
    previous_line = b""
    has_previous_line = False
    for line in lines:
        if has_previous_line:
            yield previous_line + b"\n"
        previous_line = line
        has_previous_line = True

    if not has_previous_line:
        return

    if has_trailing_newline or add_trailing_newline_when_nonempty:
        yield previous_line + b"\n"
    else:
        yield previous_line
