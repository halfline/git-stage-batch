"""Line piece-table storage for editor mutations."""

from __future__ import annotations

from array import array
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, SupportsBytes

if TYPE_CHECKING:
    from .line_editor import LineEditor


BytesLike = bytes | bytearray | memoryview
LineLike = BytesLike | SupportsBytes


@dataclass(slots=True)
class LineSource:
    """One source sequence referenced by the piece table."""

    lines: Sequence[LineLike]
    owner: LineEditor | None = None


@dataclass(slots=True)
class LineRange:
    """One indexed line range held by an editor."""

    lines: Sequence[LineLike]
    start: int
    end: int
    owner: LineEditor | None


SOURCE_RUN = 0
_INDEXED_RUN = 1
_UNKNOWN_END = (1 << 64) - 1


class LinePieceTable:
    """Compact run table for editor line content."""

    def __init__(self, source: Sequence[LineLike], owner: LineEditor) -> None:
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
    ) -> tuple[int, Sequence[LineLike], int, int | None, LineEditor | None]:
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
        owner: LineEditor | None,
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

    def replace_range(
        self,
        selection_start: int,
        selection_end: int | None,
        inserted_ranges: Sequence[LineRange],
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

    def active_owners(self) -> Iterator[LineEditor]:
        for source_id in self._run_source_ids:
            owner = self._sources[source_id].owner
            if owner is not None:
                yield owner

    def _source_id(
        self,
        lines: Sequence[LineLike],
        owner: LineEditor | None,
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
