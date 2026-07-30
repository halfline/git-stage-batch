"""Indexed line-range storage with shared-source lifetime tracking."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Generic, TypeVar, cast, overload

from .piece_table import LineLike, LineOwner, LinePieceTable, LineRange


_LineLike = LineLike

_SliceLineT = TypeVar("_SliceLineT")


class LineEditor(Sequence[_LineLike]):
    """Append indexed line ranges while preserving shared storage."""

    def __init__(self, source: Sequence[_LineLike]) -> None:
        self._pieces = LinePieceTable(source, self)
        self._line_count: int | None = None
        self._incoming_editor_leases: dict[LineEditor, _LineEditorLease] = {}
        self._outgoing_editor_leases: set[_LineEditorLease] = set()
        self._closed = False

    def __enter__(self) -> LineEditor:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

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

    def append_line_range(
        self,
        lines: Sequence[_LineLike],
        start: int,
        end: int,
        *,
        owner: LineEditor | None = None,
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

    def append_line_ranges_from_editor(
        self,
        editor: LineEditor,
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

    def _append_line_ranges(self, ranges: Sequence[LineRange]) -> None:
        self._require_range_owners_open(ranges)
        line_count = _line_ranges_line_count(ranges)
        if line_count == 0:
            return

        append_position = self._current_line_count()
        self._pieces.append_line_ranges(ranges)
        self._line_count = append_position + line_count
        self._sync_editor_leases()

    def _require_range_owners_open(self, ranges: Sequence[LineRange]) -> None:
        for line_range in ranges:
            owner = line_range.owner
            if owner is not None and owner is not self:
                owner._require_open()

    def close(self) -> None:
        """Release shared-range leases held by this collection."""
        if self._closed:
            return

        self._require_no_editor_borrowers()
        self._release_editor_leases()
        self._closed = True

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
    ) -> Iterator[LineRange]:
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
            yield LineRange(
                lines,
                source_start,
                source_stop,
                owner,
            )
            destination_position = segment_end

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
        active_sources: set[LineEditor] = set()
        for owner in self._pieces.active_owners():
            if owner is not None and owner is not self:
                active_sources.add(cast(LineEditor, owner))

        for source in active_sources:
            self._borrow_editor(source)

        for source, lease in list(self._incoming_editor_leases.items()):
            if source not in active_sources:
                lease.release()

    def _borrow_editor(self, source: LineEditor) -> None:
        self._require_open()
        source._require_open()

        if source is self or source in self._incoming_editor_leases:
            return

        lease = _LineEditorLease(source, self)
        self._incoming_editor_leases[source] = lease
        source._outgoing_editor_leases.add(lease)

    def _release_editor_leases(self) -> None:
        for lease in list(self._incoming_editor_leases.values()):
            lease.release()


class _LineEditorLease:
    """Borrow relationship between editors sharing line segments."""

    def __init__(self, source: LineEditor, target: LineEditor) -> None:
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


class _SelectedLineSliceSequence(Sequence[_SliceLineT], Generic[_SliceLineT]):
    """Lazy slice view over selected editor lines."""

    def __init__(
        self,
        parent: Sequence[_SliceLineT],
        line_slice: slice,
    ) -> None:
        if line_slice.step == 0:
            raise ValueError("slice step cannot be zero")
        self._parent = parent
        self._slice = line_slice

    def __len__(self) -> int:
        return len(range(*self._resolved_range()))

    @overload
    def __getitem__(self, index: int) -> _SliceLineT: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[_SliceLineT]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> _SliceLineT | Sequence[_SliceLineT]:
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
    owner: LineOwner | None,
    validate_end: bool = True,
) -> LineRange:
    _validate_line_range(lines, start, end, validate_end=validate_end)
    return LineRange(lines, start, end, owner)


def _line_ranges_line_count(ranges: Sequence[LineRange]) -> int:
    return sum(line_range.end - line_range.start for line_range in ranges)


def _slice_uses_negative_bounds(line_slice: slice) -> bool:
    return (
        (line_slice.start is not None and line_slice.start < 0)
        or (line_slice.stop is not None and line_slice.stop < 0)
    )
