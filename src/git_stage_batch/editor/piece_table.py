"""Line piece-table storage for editor mutations."""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, SupportsBytes


BytesLike = bytes | bytearray | memoryview
LineLike = BytesLike | SupportsBytes


class LineOwner(Protocol):
    """Owner contract retained alongside borrowed line storage."""

    def _require_open(self) -> None: ...


@dataclass(slots=True)
class LineSource:
    """One source sequence referenced by the piece table."""

    lines: Sequence[LineLike]
    owner: LineOwner | None = None


@dataclass(slots=True)
class LineRange:
    """One indexed line range held by an editor."""

    lines: Sequence[LineLike]
    start: int
    end: int
    owner: LineOwner | None


SOURCE_RUN = 0
_INDEXED_RUN = 1
_UNKNOWN_END = (1 << 64) - 1


class LinePieceTable:
    """Compact run table for editor line content."""

    def __init__(self, source: Sequence[LineLike], owner: LineOwner) -> None:
        self._sources: list[LineSource] = []
        self._source_lookup: dict[tuple[int, int], int] = {}
        self._run_kinds = bytearray()
        self._run_source_ids = array("Q")
        self._run_starts = array("Q")
        self._run_ends = array("Q")

        source_id = self._source_id(source, owner)
        self._append_run(SOURCE_RUN, source_id, 0, _UNKNOWN_END)

    def __len__(self) -> int:
        return len(self._run_kinds)

    def run(
        self,
        index: int,
    ) -> tuple[int, Sequence[LineLike], int, int | None, LineOwner | None]:
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
        lines: Sequence[LineLike],
        start: int,
        end: int,
        owner: LineOwner | None,
    ) -> None:
        source_id = self._source_id(lines, owner)
        self._append_run(_INDEXED_RUN, source_id, start, end)

    def append_line_ranges(self, ranges: Sequence[LineRange]) -> None:
        for line_range in ranges:
            self.append_line_range(
                line_range.lines,
                line_range.start,
                line_range.end,
                line_range.owner,
            )

    def _source_id(
        self,
        lines: Sequence[LineLike],
        owner: LineOwner | None,
    ) -> int:
        key = (id(lines), id(owner))
        source_id = self._source_lookup.get(key)
        if source_id is not None:
            source = self._sources[source_id]
            if source.lines is lines and source.owner is owner:
                return source_id

        source_id = len(self._sources)
        self._sources.append(LineSource(lines, owner))
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
