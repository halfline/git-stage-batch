"""Editor mutation helpers."""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import SupportsBytes, overload

from .buffer import EditorBuffer
from .line_endings import detect_line_ending, restore_line_endings_in_chunks
from ..utils.text import normalize_line_ending


class Cursor:
    """An opaque position in editor lines."""

    __slots__ = ("_editor", "_id")

    def __init__(self, editor: Editor, cursor_id: int) -> None:
        self._editor = editor
        self._id = cursor_id


_BytesLike = bytes | bytearray | memoryview
_LineLike = _BytesLike | SupportsBytes


@dataclass(slots=True)
class _LineSource:
    lines: Sequence[_LineLike]
    owner: Editor | None = None


@dataclass(slots=True)
class _LineRange:
    lines: Sequence[_LineLike]
    start: int
    end: int
    owner: Editor | None


_SOURCE_RUN = 0
_INDEXED_RUN = 1
_UNKNOWN_END = (1 << 64) - 1

_TransformResult = _BytesLike | Iterable[_LineLike]
_Selection = tuple[int, int | None]


class _LinePieceTable:
    """Compact run table for editor line content."""

    def __init__(self, source: Sequence[_LineLike], owner: Editor) -> None:
        self._sources: list[_LineSource] = []
        self._source_lookup: dict[tuple[int, int], int] = {}
        self._run_kinds = bytearray()
        self._run_source_ids = array("Q")
        self._run_starts = array("Q")
        self._run_ends = array("Q")

        source_id = self._source_id(source, owner)
        self._append_run(_SOURCE_RUN, source_id, 0, _UNKNOWN_END)

    def __len__(self) -> int:
        return len(self._run_kinds)

    def run(
        self,
        index: int,
    ) -> tuple[int, Sequence[_LineLike], int, int | None, Editor | None]:
        source = self._sources[self._run_source_ids[index]]
        end = self._run_ends[index]
        return (
            self._run_kinds[index],
            source.lines,
            self._run_starts[index],
            None if end == _UNKNOWN_END else end,
            source.owner,
        )

    def set_run_end(self, index: int, end: int) -> None:
        self._run_ends[index] = end

    def append_line_range(
        self,
        lines: Sequence[_LineLike],
        start: int,
        end: int,
        owner: Editor | None,
    ) -> None:
        source_id = self._source_id(lines, owner)
        self._append_run(_INDEXED_RUN, source_id, start, end)

    def append_line_ranges(self, ranges: Sequence[_LineRange]) -> None:
        for line_range in ranges:
            self.append_line_range(
                line_range.lines,
                line_range.start,
                line_range.end,
                line_range.owner,
            )

    def replace_range(
        self,
        selection_start: int,
        selection_end: int | None,
        inserted_ranges: Sequence[_LineRange],
    ) -> None:
        replacement_kinds = bytearray()
        replacement_source_ids = array("Q")
        replacement_starts = array("Q")
        replacement_ends = array("Q")
        inserted = False
        destination_position = 0

        def append_run(
            kind: int,
            source_id: int,
            start: int,
            end: int,
        ) -> None:
            if end != _UNKNOWN_END and end == start:
                return

            if (
                replacement_kinds
                and replacement_kinds[-1] == kind
                and replacement_source_ids[-1] == source_id
                and replacement_ends[-1] == start
            ):
                replacement_ends[-1] = end
                return

            replacement_kinds.append(kind)
            replacement_source_ids.append(source_id)
            replacement_starts.append(start)
            replacement_ends.append(end)

        def append_inserted_ranges() -> None:
            for line_range in inserted_ranges:
                source_id = self._source_id(line_range.lines, line_range.owner)
                append_run(
                    _INDEXED_RUN,
                    source_id,
                    line_range.start,
                    line_range.end,
                )

        for run_index in range(len(self)):
            kind = self._run_kinds[run_index]
            source_id = self._run_source_ids[run_index]
            run_start = self._run_starts[run_index]
            run_end = self._run_ends[run_index]
            segment_start = destination_position

            if run_end == _UNKNOWN_END:
                if selection_end is not None and selection_end <= segment_start:
                    if not inserted:
                        append_inserted_ranges()
                        inserted = True
                    append_run(kind, source_id, run_start, run_end)
                    continue

                prefix_end = max(selection_start - segment_start, 0)
                if prefix_end > 0:
                    append_run(kind, source_id, run_start, run_start + prefix_end)

                if not inserted:
                    append_inserted_ranges()
                    inserted = True

                if selection_end is not None:
                    suffix_start = max(selection_end - segment_start, 0)
                    append_run(
                        kind,
                        source_id,
                        run_start + suffix_start,
                        _UNKNOWN_END,
                    )
                continue

            segment_line_count = run_end - run_start
            segment_end = segment_start + segment_line_count
            destination_position = segment_end

            if selection_end is not None and segment_end <= selection_start:
                append_run(kind, source_id, run_start, run_end)
                continue

            if selection_end is not None and segment_start >= selection_end:
                if not inserted:
                    append_inserted_ranges()
                    inserted = True
                append_run(kind, source_id, run_start, run_end)
                continue

            prefix_end = max(selection_start - segment_start, 0)
            if prefix_end > 0:
                append_run(kind, source_id, run_start, run_start + prefix_end)

            if not inserted:
                append_inserted_ranges()
                inserted = True

            if selection_end is not None:
                suffix_start = min(selection_end - segment_start, segment_line_count)
                if suffix_start < segment_line_count:
                    append_run(
                        kind,
                        source_id,
                        run_start + suffix_start,
                        run_end,
                    )

        if not inserted:
            append_inserted_ranges()

        self._run_kinds = replacement_kinds
        self._run_source_ids = replacement_source_ids
        self._run_starts = replacement_starts
        self._run_ends = replacement_ends

    def active_owners(self) -> Iterator[Editor]:
        for source_id in self._run_source_ids:
            owner = self._sources[source_id].owner
            if owner is not None:
                yield owner

    def _source_id(
        self,
        lines: Sequence[_LineLike],
        owner: Editor | None,
    ) -> int:
        key = (id(lines), id(owner))
        source_id = self._source_lookup.get(key)
        if source_id is not None:
            source = self._sources[source_id]
            if source.lines is lines and source.owner is owner:
                return source_id

        source_id = len(self._sources)
        self._sources.append(_LineSource(lines, owner))
        self._source_lookup[key] = source_id
        return source_id

    def _append_run(
        self,
        kind: int,
        source_id: int,
        start: int,
        end: int,
    ) -> None:
        if end != _UNKNOWN_END and end == start:
            return

        if (
            self._run_kinds
            and self._run_kinds[-1] == kind
            and self._run_source_ids[-1] == source_id
            and self._run_ends[-1] == start
        ):
            self._run_ends[-1] = end
            return

        self._run_kinds.append(kind)
        self._run_source_ids.append(source_id)
        self._run_starts.append(start)
        self._run_ends.append(end)


class Editor(Sequence[_LineLike]):
    """Stateful line editor for indexed lines."""

    def __init__(self, source: Sequence[_LineLike]) -> None:
        self._source = source
        self._pieces = _LinePieceTable(source, self)
        self._line_count: int | None = None
        self._position = 0
        self._selection: _Selection | None = None
        self._cursor_positions: dict[int, int] = {}
        self._next_cursor_id = 0
        self._owned_buffers: list[EditorBuffer] = []
        self._incoming_editor_leases: dict[Editor, _EditorLease] = {}
        self._outgoing_editor_leases: set[_EditorLease] = set()
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

    @overload
    def __getitem__(self, index: int) -> _LineLike: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_LineLike]: ...

    def __getitem__(self, index: int | slice) -> _LineLike | Sequence[_LineLike]:
        self._require_open()
        if isinstance(index, slice):
            return _SelectedLineSliceSequence(self, index)

        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)

        try:
            return self._line_at_position(index)
        except IndexError as exc:
            raise IndexError(index) from exc

    def __len__(self) -> int:
        self._require_open()
        return self._current_line_count()

    def line_chunks(self) -> Iterator[bytes]:
        """Yield exact edited lines as byte chunks."""
        self._require_open()
        for line in self._lines():
            yield bytes(line)

    def add_line(self, payload: _LineLike) -> None:
        """Insert or replace with one line."""
        self.add_lines((payload,))

    def add_lines(
        self,
        lines: Iterable[_LineLike],
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        """Insert or replace with generated lines or an indexed range."""
        self._require_open()
        range_requested = start is not None or end is not None
        range_start, range_end = _line_range_bounds(start, end)
        if range_requested and isinstance(lines, Sequence):
            validate_end = range_end is not None
            self._add_line_range(
                lines,
                range_start,
                len(lines) if range_end is None else range_end,
                validate_end=validate_end,
            )
            return

        inserted_lines = _spool_inserted_lines(
            lines,
            start=range_start,
            end=range_end,
            owner=self,
        )
        if inserted_lines.owned_buffer is not None:
            self._owned_buffers.append(inserted_lines.owned_buffer)
        self._commit_edit(inserted_lines)

    def append_line_range(
        self,
        lines: Sequence[_LineLike],
        start: int,
        end: int,
        *,
        owner: Editor | None = None,
    ) -> None:
        """Append an indexed line range without walking existing content."""
        self._require_open()
        line_range = _validated_line_range(
            lines,
            start,
            end,
            owner=owner or self,
        )
        self._append_line_ranges((line_range,))

    def append_line_ranges(self, ranges: Iterable[object]) -> None:
        """Append indexed line ranges without selection replacement."""
        self._require_open()
        line_ranges = _coerce_line_ranges(ranges, default_owner=self)
        self._append_line_ranges(line_ranges)

    def append_line_ranges_from_editor(
        self,
        editor: Editor,
        start: int,
        end: int,
    ) -> None:
        """Append a range from another editor without selection replacement."""
        self._require_open()
        editor._require_open()
        if start < 0 or end < start:
            raise ValueError("invalid line range")
        if end > len(editor):
            raise ValueError("invalid line range")

        self._append_line_ranges(tuple(editor._line_sources(start, end)))

    def add_lines_from_editor(
        self,
        editor: Editor,
        start: int,
        end: int,
    ) -> None:
        """Insert or replace with a range from another editor."""
        self.add_line_ranges_from_editor(editor, start, end)

    def add_line_ranges_from_editor(
        self,
        editor: Editor,
        start: int,
        end: int,
    ) -> None:
        """Insert or replace with ranges from another editor in one edit."""
        self._require_open()
        editor._require_open()
        if start < 0 or end < start:
            raise ValueError("invalid line range")
        if end > len(editor):
            raise ValueError("invalid line range")

        self.replace_selection_with_ranges(editor._line_sources(start, end))

    def replace_selection_with_ranges(self, ranges: Iterable[object]) -> None:
        """Replace the current selection with indexed line ranges."""
        self._require_open()
        line_ranges = _coerce_line_ranges(ranges, default_owner=self)
        self._commit_edit(
            _InsertedLines(
                ranges=line_ranges,
                line_count=_line_ranges_line_count(line_ranges),
            )
        )

    def _add_line_range(
        self,
        lines: Sequence[_LineLike],
        start: int,
        end: int,
        *,
        owner: Editor | None = None,
        validate_end: bool = True,
    ) -> None:
        _validate_line_range(lines, start, end, validate_end=validate_end)

        self._commit_edit(
            _InsertedLines(
                ranges=(
                    _LineRange(lines, start, end, owner or self),
                ),
                line_count=end - start,
            )
        )

    def _append_line_ranges(self, ranges: Sequence[_LineRange]) -> None:
        self._require_range_owners_open(ranges)
        line_count = _line_ranges_line_count(ranges)
        if line_count == 0:
            self._position = self._current_line_count()
            self._selection = None
            return

        append_position = self._current_line_count()
        self._pieces.append_line_ranges(ranges)
        self._shift_cursors(append_position, append_position, line_count)
        self._line_count = append_position + line_count
        self._position = self._line_count
        self._selection = None
        self._sync_editor_leases()

    def _require_range_owners_open(self, ranges: Sequence[_LineRange]) -> None:
        for line_range in ranges:
            owner = line_range.owner
            if owner is not None and owner is not self:
                owner._require_open()

    def add_bytes(self, data: bytes) -> None:
        """Insert raw bytes split on line endings."""
        self._require_open()
        if not isinstance(data, bytes):
            raise TypeError(f"expected bytes object, got {type(data).__name__}")

        buffer = EditorBuffer.from_bytes(data)
        line_count = _count_lines_in_bytes(data)
        self._owned_buffers.append(buffer)
        self._commit_edit(
            _InsertedLines(
                ranges=(
                    _LineRange(
                        buffer,
                        0,
                        line_count,
                        self,
                    ),
                ),
                line_count=line_count,
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
        self._require_no_editor_borrowers()
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

        self._require_no_editor_borrowers()
        self._release_editor_leases()
        for buffer in self._owned_buffers:
            buffer.close()
        self._closed = True

    def _commit_edit(self, inserted_lines: _InsertedLines | None) -> None:
        selection_start, selection_end = self._selected_range()
        inserted_line_count = (
            inserted_lines.line_count
            if inserted_lines is not None
            else 0
        )
        inserted_ranges = (
            inserted_lines.ranges
            if inserted_lines is not None and inserted_lines.line_count > 0
            else ()
        )
        self._require_range_owners_open(inserted_ranges)

        self._replace_selected_range(
            selection_start,
            selection_end,
            inserted_ranges,
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
        self._sync_editor_leases()

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
        inserted_ranges: Sequence[_LineRange],
    ) -> None:
        self._pieces.replace_range(selection_start, selection_end, inserted_ranges)

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
        for line in self._lines():
            yield _line_body(line)

    def _lines(self) -> Iterator[_LineLike]:
        for run_index in range(len(self._pieces)):
            _kind, lines, start, end, _owner = self._pieces.run(run_index)
            if end is None:
                index = start
                while True:
                    try:
                        line = lines[index]
                    except IndexError:
                        break
                    yield line
                    index += 1
            else:
                for index in range(start, end):
                    yield lines[index]

    def _line_at_position(self, position: int) -> _LineLike:
        destination_position = 0

        for run_index in range(len(self._pieces)):
            _kind, lines, start, end, _owner = self._pieces.run(run_index)
            segment_start = destination_position
            if end is None:
                if position >= segment_start:
                    return lines[start + (position - segment_start)]
                raise IndexError(position)

            segment_line_count = end - start
            segment_end = segment_start + segment_line_count
            if segment_start <= position < segment_end:
                segment_index = start + (position - segment_start)
                return lines[segment_index]
            destination_position = segment_end

        raise IndexError(position)

    def _line_sources(
        self,
        start: int,
        end: int,
    ) -> Iterator[_LineRange]:
        self._current_line_count()

        destination_position = 0
        for run_index in range(len(self._pieces)):
            _kind, lines, segment_start, segment_end, owner = self._pieces.run(
                run_index
            )
            if segment_end is None:
                raise ValueError("source run has unknown line count")

            segment_line_count = segment_end - segment_start
            segment_end = destination_position + segment_line_count

            if segment_end <= start:
                destination_position = segment_end
                continue
            if destination_position >= end:
                break

            range_start = max(start, destination_position)
            range_end = min(end, segment_end)
            source_start = segment_start + (range_start - destination_position)
            source_stop = segment_start + (range_end - destination_position)
            yield _LineRange(
                lines,
                source_start,
                source_stop,
                owner,
            )
            destination_position = segment_end

    def _destination_position_for_source_line(self, line: int) -> int | None:
        destination_position = 0

        for run_index in range(len(self._pieces)):
            kind, _lines, start, end, _owner = self._pieces.run(run_index)
            if end is None:
                if (
                    kind == _SOURCE_RUN
                    and line >= start
                    and self._source_boundary_exists(line)
                ):
                    return destination_position + (line - start)
                return None

            segment_line_count = end - start
            if kind == _SOURCE_RUN and start <= line <= end:
                return destination_position + (line - start)
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
        for run_index in range(len(self._pieces)):
            _kind, _lines, start, end, _owner = self._pieces.run(run_index)
            if end is None:
                source_line = start + (line - destination_position)
                if line >= destination_position and self._source_boundary_exists(
                    source_line
                ):
                    return line
                raise ValueError("destination line is out of range")

            segment_line_count = end - start
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
        for run_index in range(len(self._pieces)):
            _kind, lines, start, end, _owner = self._pieces.run(run_index)
            if end is None:
                source_line_count = len(lines)
                self._pieces.set_run_end(run_index, source_line_count)
                line_count += source_line_count - start
            else:
                line_count += end - start

        self._line_count = line_count
        return line_count

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("editor is closed")

    def _require_no_editor_borrowers(self) -> None:
        if self._outgoing_editor_leases:
            raise ValueError("editor has active leases")

    def _sync_editor_leases(self) -> None:
        active_sources: set[Editor] = set()
        for owner in self._pieces.active_owners():
            if owner is not None and owner is not self:
                active_sources.add(owner)

        for source in active_sources:
            self._borrow_editor(source)

        for source, lease in list(self._incoming_editor_leases.items()):
            if source not in active_sources:
                lease.release()

    def _borrow_editor(self, source: Editor) -> None:
        self._require_open()
        source._require_open()

        if source is self or source in self._incoming_editor_leases:
            return

        lease = _EditorLease(source, self)
        self._incoming_editor_leases[source] = lease
        source._outgoing_editor_leases.add(lease)

    def _release_editor_leases(self) -> None:
        for lease in list(self._incoming_editor_leases.values()):
            lease.release()


@dataclass(slots=True)
class _InsertedLines:
    ranges: Sequence[_LineRange]
    line_count: int
    owned_buffer: EditorBuffer | None = None


class _EditorLease:
    """Borrow relationship between editors sharing line segments."""

    def __init__(self, source: Editor, target: Editor) -> None:
        self._source = source
        self._target = target
        self._released = False

    def release(self) -> None:
        if self._released:
            return

        self._released = True
        if self._target._incoming_editor_leases.get(self._source) is self:
            del self._target._incoming_editor_leases[self._source]
        self._source._outgoing_editor_leases.discard(self)


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
                bytes(self._editor._line_at_position(self._selection_start + index))
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
    source_lines: Sequence[_LineLike],
    edited_lines: Iterable[_LineLike],
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


def export_lines_as_buffer(
    lines: Iterable[_LineLike],
    *,
    has_trailing_newline: bool = True,
    add_trailing_newline_when_nonempty: bool = False,
    line_endings_from: Sequence[bytes] | None = None,
) -> EditorBuffer:
    """Export generated lines to a buffer without editor state."""
    chunks = _line_body_chunks(
        (_line_body(line) for line in lines),
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
    return EditorBuffer.from_chunks(chunks)


def _line_range_bounds(
    start: int | None,
    end: int | None,
) -> tuple[int, int | None]:
    range_start = 0 if start is None else start
    if range_start < 0 or (end is not None and end < range_start):
        raise ValueError("invalid line range")
    return range_start, end


def _validate_line_range(
    lines: Sequence[_LineLike],
    start: int,
    end: int,
    *,
    validate_end: bool,
) -> None:
    if start < 0 or end < start:
        raise ValueError("invalid line range")

    if not validate_end or end == start:
        return

    try:
        lines[end - 1]
    except IndexError as exc:
        raise ValueError("invalid line range") from exc


def _validated_line_range(
    lines: Sequence[_LineLike],
    start: int,
    end: int,
    *,
    owner: Editor | None,
    validate_end: bool = True,
) -> _LineRange:
    _validate_line_range(lines, start, end, validate_end=validate_end)
    return _LineRange(lines, start, end, owner)


def _coerce_line_ranges(
    ranges: Iterable[object],
    *,
    default_owner: Editor,
) -> tuple[_LineRange, ...]:
    return tuple(_coerce_line_range(item, default_owner) for item in ranges)


def _coerce_line_range(
    line_range: object,
    default_owner: Editor,
) -> _LineRange:
    if isinstance(line_range, _LineRange):
        owner = line_range.owner or default_owner
        return _validated_line_range(
            line_range.lines,
            line_range.start,
            line_range.end,
            owner=owner,
            validate_end=False,
        )

    try:
        values = tuple(line_range)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("expected line range tuple") from exc

    if len(values) == 3:
        lines, start, end = values
        owner = default_owner
    elif len(values) == 4:
        lines, start, end, owner = values
        owner = owner or default_owner
    else:
        raise TypeError("expected line range tuple")

    if not isinstance(lines, Sequence):
        raise TypeError("expected line sequence in range")
    if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("expected integer line range bounds")
    if owner is not None and not isinstance(owner, Editor):
        raise TypeError("expected editor owner")

    return _validated_line_range(lines, start, end, owner=owner)


def _line_ranges_line_count(ranges: Sequence[_LineRange]) -> int:
    return sum(line_range.end - line_range.start for line_range in ranges)


def _spool_inserted_lines(
    lines: Iterable[_LineLike],
    *,
    start: int = 0,
    end: int | None = None,
    owner: Editor | None = None,
) -> _InsertedLines:
    if start < 0 or (end is not None and end < start):
        raise ValueError("invalid line range")

    line_count = 0

    def chunks() -> Iterator[bytes]:
        nonlocal line_count
        for index, line in enumerate(lines):
            if index < start:
                continue
            if end is not None and index >= end:
                break
            line_count += 1
            yield _line_body(line) + b"\n"

    buffer = EditorBuffer.from_chunks(chunks())
    return _InsertedLines(
        ranges=(
            _LineRange(buffer, 0, line_count, owner),
        ),
        line_count=line_count,
        owned_buffer=buffer,
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


def _line_body(line: _LineLike) -> bytes:
    line_bytes = _line_bytes(line)
    if line_bytes.endswith(b"\r\n"):
        return line_bytes[:-2]
    if line_bytes.endswith(b"\n"):
        return line_bytes[:-1]
    return line_bytes


def _line_bytes(line: _LineLike) -> bytes:
    if isinstance(line, (bytes, bytearray, memoryview)):
        return bytes(line)
    if hasattr(line, "__bytes__"):
        return bytes(line)
    raise TypeError(f"expected bytes-compatible line, got {type(line).__name__}")


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
