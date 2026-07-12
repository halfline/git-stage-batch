"""Compact realized batch entry storage."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from ..editor.line_editor import LineEditor
from .line_matching.line_range_view import LineRangeView as _LineRangeView
from .realized_entries import RealizedEntry as _RealizedEntry
from .realized_provenance import (
    PROVENANCE_RUN_CLAIMED as _PROVENANCE_CLAIMED_FLAG,
    ProvenanceRun as _RealizedProvenanceRun,
    ProvenanceRunTable as _RealizedProvenanceTable,
    line_number_or_none as _provenance_line_number_or_none,
    stored_line_number as _stored_provenance_line_number,
)


class RealizedEntries(Sequence[_RealizedEntry]):
    """Compact realized content with run-length provenance storage.

    Indexing returns RealizedEntry views for existing helper contracts. Streaming
    and internal lookups use direct accessors so the result does not retain one
    Python object per output line.
    """

    def __init__(self, entries: Iterable[_RealizedEntry] = ()) -> None:
        self._editor = LineEditor(())
        self._provenance = _RealizedProvenanceTable()
        self._line_count = 0
        self._closed = False

        for entry in entries:
            self.append_entry(entry)

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        self._require_open()
        return self._line_count

    def __getitem__(self, index: int | slice) -> _RealizedEntry | RealizedEntries:
        self._require_open()
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return self.slice(start, stop)

            result = RealizedEntries()
            for child_index in range(start, stop, step):
                result.append_from(self, child_index)
            return result

        index = self._normalize_index(index)
        return _RealizedEntry(
            content=self._editor[index],
            source_line=self.source_line_at(index),
            target_line=self.target_line_at(index),
            is_claimed=self.is_claimed_at(index),
        )

    def append(
        self,
        content: Any,
        *,
        source_line: int | None = None,
        target_line: int | None = None,
        is_claimed: bool = False,
    ) -> None:
        self.append_line_range_from(
            (content,),
            0,
            1,
            source_line_start=source_line,
            target_line_start=target_line,
            is_claimed=is_claimed,
        )

    def append_line_range_from(
        self,
        lines: Sequence[Any],
        start: int,
        end: int,
        *,
        source_line_start: int | None = None,
        target_line_start: int | None = None,
        is_claimed: bool = False,
    ) -> None:
        self._require_open()
        if start < 0 or end < start:
            raise ValueError("invalid line range")
        if start == end:
            return

        if isinstance(lines, LineEditor):
            self._editor.append_line_ranges_from_editor(lines, start, end)
        else:
            self._editor.append_line_range(lines, start, end)

        dest_start = self._line_count
        dest_end = dest_start + (end - start)
        self._provenance.append(
            dest_start,
            dest_end,
            source_start=_stored_provenance_line_number(source_line_start),
            target_start=_stored_provenance_line_number(target_line_start),
            flags=_PROVENANCE_CLAIMED_FLAG if is_claimed else 0,
        )
        self._line_count = dest_end

    def append_line_from(
        self,
        lines: Sequence[Any],
        index: int,
        *,
        source_line: int | None = None,
        target_line: int | None = None,
        is_claimed: bool = False,
    ) -> None:
        self.append_line_range_from(
            lines,
            index,
            index + 1,
            source_line_start=source_line,
            target_line_start=target_line,
            is_claimed=is_claimed,
        )

    def append_entry(self, entry: _RealizedEntry) -> None:
        self.append(
            entry.content,
            source_line=entry.source_line,
            target_line=entry.target_line,
            is_claimed=entry.is_claimed,
        )

    def append_from(
        self,
        entries: Sequence[_RealizedEntry],
        index: int,
    ) -> None:
        if isinstance(entries, RealizedEntries):
            index = entries._normalize_index(index)
            self.copy_slice_from(entries, index, index + 1)
            return

        self.append_entry(entries[index])

    def copy_slice_from(
        self,
        entries: Sequence[_RealizedEntry],
        start: int,
        stop: int,
    ) -> None:
        self._require_open()
        if isinstance(entries, RealizedEntries):
            entries._require_open()
            start, stop = entries._validated_range(start, stop)
            for run in entries.provenance_runs(start, stop):
                self.append_line_range_from(
                    entries._editor,
                    run.dest_start,
                    run.dest_end,
                    source_line_start=_provenance_line_number_or_none(
                        run.source_start,
                    ),
                    target_line_start=_provenance_line_number_or_none(
                        run.target_start,
                    ),
                    is_claimed=run.is_claimed,
                )
            return

        if start < 0 or stop < start or stop > len(entries):
            raise ValueError("invalid line range")
        for index in range(start, stop):
            self.append_entry(entries[index])

    def provenance_runs(
        self,
        start: int = 0,
        stop: int | None = None,
    ) -> Iterator[_RealizedProvenanceRun]:
        self._require_open()
        if stop is None:
            stop = len(self)
        start, stop = self._validated_range(start, stop)
        yield from self._provenance.runs(start, stop)

    @property
    def provenance_run_count(self) -> int:
        self._require_open()
        return len(self._provenance)

    @property
    def flushed_provenance_run_count(self) -> int:
        self._require_open()
        return self._provenance.flushed_run_count

    def content_at(self, index: int) -> Any:
        self._require_open()
        return self._editor[self._normalize_index(index)]

    def source_line_at(self, index: int) -> int | None:
        self._require_open()
        index = self._normalize_index(index)
        return self._provenance.run_at(index).source_line_at(index)

    def target_line_at(self, index: int) -> int | None:
        self._require_open()
        index = self._normalize_index(index)
        return self._provenance.run_at(index).target_line_at(index)

    def is_claimed_at(self, index: int) -> bool:
        self._require_open()
        index = self._normalize_index(index)
        return self._provenance.run_at(index).is_claimed

    def content_chunks(self) -> Iterator[bytes]:
        self._require_open()
        yield from self._editor.line_chunks()

    def slice(self, start: int, stop: int) -> RealizedEntries:
        self._require_open()
        result = RealizedEntries()
        result.copy_slice_from(self, *self._validated_range(start, stop))
        return result

    def without_range(self, start: int, stop: int) -> RealizedEntries:
        self._require_open()
        start, stop = self._validated_range(start, stop)
        result = RealizedEntries()
        result.copy_slice_from(self, 0, start)
        result.copy_slice_from(self, stop, len(self))
        return result

    def insert_entries(
        self,
        position: int,
        entries: Sequence[_RealizedEntry],
    ) -> RealizedEntries:
        self._require_open()
        position = self._validated_position(position)
        result = RealizedEntries()
        result.copy_slice_from(self, 0, position)
        result.copy_slice_from(entries, 0, len(entries))
        result.copy_slice_from(self, position, len(self))
        return result

    def _append_range_from(
        self,
        entries: Sequence[_RealizedEntry],
        start: int,
        stop: int,
    ) -> None:
        self.copy_slice_from(entries, start, stop)

    def close(self) -> None:
        if self._closed:
            return

        self._provenance.close()
        try:
            self._editor.close()
        except ValueError:
            # A returned entries object may still borrow ranges from this
            # editor. In that case closing is deferred to the borrower
            # lifetime; public access to this wrapper is still rejected.
            pass
        self._closed = True

    def __enter__(self) -> RealizedEntries:
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
        self._require_open()
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return index

    def _validated_range(self, start: int, stop: int) -> tuple[int, int]:
        if start < 0:
            start += len(self)
        if stop < 0:
            stop += len(self)
        if start < 0 or stop < start or stop > len(self):
            raise ValueError("invalid line range")
        return start, stop

    def _validated_position(self, position: int) -> int:
        if position < 0:
            position += len(self)
        if position < 0 or position > len(self):
            raise ValueError("invalid insert position")
        return position

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("realized entries are closed")


def as_realized_entries(entries: Sequence[_RealizedEntry]) -> RealizedEntries:
    if isinstance(entries, RealizedEntries):
        return entries
    return RealizedEntries(entries)


def realized_entry_content_at(entries: Sequence[_RealizedEntry], index: int) -> Any:
    if isinstance(entries, RealizedEntries):
        return entries.content_at(index)
    return entries[index].content


def realized_entry_source_line_at(
    entries: Sequence[_RealizedEntry],
    index: int,
) -> int | None:
    if isinstance(entries, RealizedEntries):
        return entries.source_line_at(index)
    return entries[index].source_line


def realized_entry_target_line_at(
    entries: Sequence[_RealizedEntry],
    index: int,
) -> int | None:
    if isinstance(entries, RealizedEntries):
        return entries.target_line_at(index)
    return entries[index].target_line


def realized_entry_is_claimed_at(
    entries: Sequence[_RealizedEntry],
    index: int,
) -> bool:
    if isinstance(entries, RealizedEntries):
        return entries.is_claimed_at(index)
    return entries[index].is_claimed


class RealizedEntryContentSequence(Sequence[bytes]):
    """Indexed view over realized entry content."""

    def __init__(self, entries: Sequence[_RealizedEntry]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return _LineRangeView(self, start, stop)
            return tuple(
                self[child_index]
                for child_index in range(start, stop, step)
            )

        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return realized_entry_content_at(self._entries, index)


def backing_content_sequence(lines: Sequence[bytes]) -> Sequence[Any]:
    if (
        isinstance(lines, RealizedEntryContentSequence)
        and isinstance(lines._entries, RealizedEntries)
    ):
        return lines._entries._editor
    return lines


def realized_entry_content_chunks(
    entries: Iterable[_RealizedEntry],
) -> Iterator[bytes]:
    """Yield content bytes from realized entries."""
    if isinstance(entries, RealizedEntries):
        yield from entries.content_chunks()
        return

    for entry in entries:
        yield bytes(entry.content)
