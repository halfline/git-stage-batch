"""Compact line lineage for refreshed batch sources."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from ...core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ...core.mapped_storage import (
    ChunkedMappedRecordVector,
    MappedRecordVector,
    sort_mapped_records,
)


_LINEAGE_RECORD_FORMAT = "QQQ"
_LINEAGE_CHUNK_CAPACITY = 8192
_LINEAGE_OLD_START = 0
_LINEAGE_OLD_END = 1
_LINEAGE_NEW_START = 2
_EXPANSION_RECORD_FORMAT = "QQQQ"
_EXPANSION_SOURCE_START = 0
_EXPANSION_SOURCE_END = 1
_EXPANSION_NEW_START = 2
_EXPANSION_NEW_END = 3
_TRANSLATED_RANGE_RECORD_FORMAT = "QQ"


@dataclass(frozen=True, slots=True)
class LineageRun:
    """Contiguous line translation from one coordinate space to another."""

    old_start: int
    old_end: int
    new_start: int

    def __post_init__(self) -> None:
        if self.old_start <= 0 or self.old_end <= 0 or self.new_start <= 0:
            raise ValueError("lineage coordinates must be positive")
        if self.old_start > self.old_end:
            raise ValueError("lineage run start must be <= end")

    @property
    def new_end(self) -> int:
        return self.new_start + (self.old_end - self.old_start)

    def translate(self, old_line: int) -> int | None:
        if self.old_start <= old_line <= self.old_end:
            return self.new_start + (old_line - self.old_start)
        return None

    def translate_range(self, old_start: int, old_end: int) -> tuple[int, int]:
        if old_start < self.old_start or old_end > self.old_end:
            raise ValueError("range is outside lineage run")
        new_start = self.new_start + (old_start - self.old_start)
        return new_start, new_start + (old_end - old_start)


@dataclass(frozen=True, slots=True)
class SourceSelectionExpansion:
    """A fully owned source range expanded to a longer refreshed range."""

    source_start: int
    source_end: int
    new_start: int
    new_end: int

    def __post_init__(self) -> None:
        if (
            self.source_start <= 0
            or self.source_end <= 0
            or self.new_start <= 0
            or self.new_end <= 0
        ):
            raise ValueError("source expansion coordinates must be positive")
        if self.source_start > self.source_end:
            raise ValueError("source expansion start must be <= end")
        if self.new_start > self.new_end:
            raise ValueError("source expansion destination start must be <= end")
        source_count = self.source_end - self.source_start + 1
        new_count = self.new_end - self.new_start + 1
        if new_count <= source_count:
            raise ValueError("source expansion destination must be longer")


def _lineage_run_from_record(record: tuple[int, ...]) -> LineageRun:
    return LineageRun(
        record[_LINEAGE_OLD_START],
        record[_LINEAGE_OLD_END],
        record[_LINEAGE_NEW_START],
    )


def _lineage_runs_can_merge(left: LineageRun, right: LineageRun) -> bool:
    return (
        right.old_start == left.old_end + 1
        and right.new_start == left.new_end + 1
    )


class _LineageRunTable:
    """Append-only mapped lineage runs with one pending Python run."""

    def __init__(
        self,
        runs: Iterable[LineageRun] = (),
        *,
        spool_dir: str | Path | None = None,
    ) -> None:
        self._runs = ChunkedMappedRecordVector(
            record_format=_LINEAGE_RECORD_FORMAT,
            chunk_capacity=_LINEAGE_CHUNK_CAPACITY,
            spool_dir=spool_dir,
        )
        self._pending_run: LineageRun | None = None
        self._closed = False

        for run in sorted(runs, key=lambda item: (item.old_start, item.old_end)):
            self.append(run)

    @property
    def byte_count(self) -> int:
        if self._closed:
            return 0
        return self._runs.byte_count

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        self._require_open()
        return len(self._runs) + (1 if self._pending_run is not None else 0)

    def append(self, run: LineageRun) -> None:
        self._require_open()
        pending = self._pending_run
        if pending is None:
            self._pending_run = run
            return

        if run.old_start <= pending.old_end:
            raise ValueError("lineage runs must not overlap")

        if _lineage_runs_can_merge(pending, run):
            self._pending_run = LineageRun(
                old_start=pending.old_start,
                old_end=run.old_end,
                new_start=pending.new_start,
            )
            return

        self._flush_pending()
        self._pending_run = run

    def run_at(self, old_line: int) -> LineageRun | None:
        self._require_open()
        if type(old_line) is not int:
            return None

        pending = self._pending_run
        if (
            pending is not None
            and pending.old_start <= old_line <= pending.old_end
        ):
            return pending

        low = 0
        high = len(self._runs)
        while low < high:
            mid = (low + high) // 2
            record = self._runs[mid]
            if old_line < record[_LINEAGE_OLD_START]:
                high = mid
            elif old_line > record[_LINEAGE_OLD_END]:
                low = mid + 1
            else:
                return _lineage_run_from_record(record)
        return None

    def runs(self) -> Iterator[LineageRun]:
        self._require_open()
        for index in range(len(self._runs)):
            yield _lineage_run_from_record(self._runs[index])
        if self._pending_run is not None:
            yield self._pending_run

    def translate_line(self, old_line: int) -> int | None:
        run = self.run_at(old_line)
        if run is None:
            return None
        return run.translate(old_line)

    def translate_range(
        self,
        old_start: int,
        old_end: int,
    ) -> tuple[int, int] | None:
        """Translate a range only when one lineage run covers it wholly."""
        if old_end < old_start:
            return None
        run = self.run_at(old_start)
        if run is None or old_end > run.old_end:
            return None
        return run.translate_range(old_start, old_end)

    def append_translated_ranges(
        self,
        selection: LineRanges,
        destination: MappedRecordVector,
    ) -> None:
        """Append translated intersections without retaining Python records."""
        run_index = 0
        for selected_start, selected_end in selection.ranges():
            while (
                run_index < len(self)
                and self._run_at_index(run_index).old_end < selected_start
            ):
                run_index += 1

            scan_index = run_index
            while scan_index < len(self):
                run = self._run_at_index(scan_index)
                if run.old_start > selected_end:
                    break
                old_start = max(selected_start, run.old_start)
                old_end = min(selected_end, run.old_end)
                if old_start <= old_end:
                    destination.append(run.translate_range(old_start, old_end))
                if run.old_end >= selected_end:
                    break
                scan_index += 1

    def first_unmapped_line(
        self,
        selection: LineSelection | Iterable[int],
    ) -> int | None:
        self._require_open()
        run_index = 0

        for selected_start, selected_end in coerce_line_ranges(selection).ranges():
            current_line = selected_start
            while (
                run_index < len(self)
                and self._run_at_index(run_index).old_end < current_line
            ):
                run_index += 1

            while current_line <= selected_end:
                if run_index >= len(self):
                    return current_line
                run = self._run_at_index(run_index)
                if run.old_start > current_line:
                    return current_line
                current_line = min(run.old_end, selected_end) + 1
                if current_line <= selected_end:
                    run_index += 1

        return None

    def close(self) -> None:
        if self._closed:
            return
        self._pending_run = None
        self._runs.close()
        self._closed = True

    def __enter__(self) -> _LineageRunTable:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _flush_pending(self) -> None:
        pending = self._pending_run
        if pending is None:
            return
        self._runs.append((
            pending.old_start,
            pending.old_end,
            pending.new_start,
        ))
        self._pending_run = None

    def _run_at_index(self, index: int) -> LineageRun:
        flushed_count = len(self._runs)
        if 0 <= index < flushed_count:
            return _lineage_run_from_record(self._runs[index])
        if index == flushed_count and self._pending_run is not None:
            return self._pending_run
        raise IndexError(index)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("lineage run table is closed")


def _source_expansion_from_record(
    record: tuple[int, ...],
) -> SourceSelectionExpansion:
    return SourceSelectionExpansion(
        source_start=record[_EXPANSION_SOURCE_START],
        source_end=record[_EXPANSION_SOURCE_END],
        new_start=record[_EXPANSION_NEW_START],
        new_end=record[_EXPANSION_NEW_END],
    )


class _SourceExpansionTable:
    """Ordered mapped expansions used only for whole-selection translation."""

    def __init__(
        self,
        expansions: Iterable[SourceSelectionExpansion] = (),
        *,
        spool_dir: str | Path | None = None,
    ) -> None:
        self._expansions = ChunkedMappedRecordVector(
            record_format=_EXPANSION_RECORD_FORMAT,
            chunk_capacity=_LINEAGE_CHUNK_CAPACITY,
            spool_dir=spool_dir,
        )
        self._last_source_end = 0
        self._closed = False
        for expansion in expansions:
            self.append(expansion)

    @property
    def byte_count(self) -> int:
        return 0 if self._closed else self._expansions.byte_count

    def __bool__(self) -> bool:
        self._require_open()
        return bool(self._expansions)

    def __len__(self) -> int:
        self._require_open()
        return len(self._expansions)

    def append(self, expansion: SourceSelectionExpansion) -> None:
        self._require_open()
        if expansion.source_start <= self._last_source_end:
            raise ValueError("source expansions must not overlap")
        self._expansions.append((
            expansion.source_start,
            expansion.source_end,
            expansion.new_start,
            expansion.new_end,
        ))
        self._last_source_end = expansion.source_end

    def runs(self) -> Iterator[SourceSelectionExpansion]:
        self._require_open()
        for record in self._expansions:
            yield _source_expansion_from_record(record)

    def translated_ranges(
        self,
        selection: LineRanges,
    ) -> Iterator[tuple[int, int]]:
        """Yield destinations only when the complete source range is selected."""
        selected_ranges = selection.ranges()
        selected_index = 0
        for record in self._expansions:
            expansion = _source_expansion_from_record(record)
            while (
                selected_index < len(selected_ranges)
                and selected_ranges[selected_index][1] < expansion.source_start
            ):
                selected_index += 1
            if selected_index >= len(selected_ranges):
                return
            selected_start, selected_end = selected_ranges[selected_index]
            if (
                selected_start <= expansion.source_start
                and selected_end >= expansion.source_end
            ):
                yield expansion.new_start, expansion.new_end

    def append_translated_ranges(
        self,
        selection: LineRanges,
        destination: MappedRecordVector,
    ) -> None:
        """Append whole-selection expansion destinations."""
        for new_start, new_end in self.translated_ranges(selection):
            destination.append((new_start, new_end))

    def close(self) -> None:
        if self._closed:
            return
        self._expansions.close()
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("source expansion table is closed")


class BatchSourceLineage:
    """Lineage from old source and working lines to refreshed source lines."""

    def __init__(
        self,
        source_runs: Iterable[LineageRun] = (),
        working_runs: Iterable[LineageRun] = (),
        source_expansions: Iterable[SourceSelectionExpansion] = (),
        *,
        spool_dir: str | Path | None = None,
    ) -> None:
        source_run_table = _LineageRunTable(
            source_runs,
            spool_dir=spool_dir,
        )
        try:
            working_run_table = _LineageRunTable(
                working_runs,
                spool_dir=spool_dir,
            )
            try:
                source_expansion_table = _SourceExpansionTable(
                    source_expansions,
                    spool_dir=spool_dir,
                )
            except BaseException:
                working_run_table.close()
                raise
        except BaseException:
            source_run_table.close()
            raise

        self._source_runs = source_run_table
        self._working_runs = working_run_table
        self._source_expansions = source_expansion_table
        self._spool_dir = spool_dir
        self._closed = False

    @property
    def byte_count(self) -> int:
        if self._closed:
            return 0
        return (
            self._source_runs.byte_count
            + self._working_runs.byte_count
            + self._source_expansions.byte_count
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def source_runs(self) -> Iterator[LineageRun]:
        self._require_open()
        return self._source_runs.runs()

    def working_runs(self) -> Iterator[LineageRun]:
        self._require_open()
        return self._working_runs.runs()

    def source_expansions(self) -> Iterator[SourceSelectionExpansion]:
        self._require_open()
        return self._source_expansions.runs()

    def append_source_run(self, run: LineageRun) -> None:
        self._require_open()
        self._source_runs.append(run)

    def append_working_run(self, run: LineageRun) -> None:
        self._require_open()
        self._working_runs.append(run)

    def append_source_expansion(
        self,
        expansion: SourceSelectionExpansion,
    ) -> None:
        self._require_open()
        self._source_expansions.append(expansion)

    def translate_source_line(self, line_number: int) -> int | None:
        self._require_open()
        return self._source_runs.translate_line(line_number)

    def translate_source_selection(
        self,
        selection: LineSelection | Iterable[int],
    ) -> LineRanges:
        self._require_open()
        source_selection = coerce_line_ranges(selection)
        source_ranges = MappedRecordVector(
            len(self._source_runs) + len(source_selection.ranges()),
            _TRANSLATED_RANGE_RECORD_FORMAT,
            spool_dir=self._spool_dir,
        )
        try:
            self._source_runs.append_translated_ranges(
                source_selection,
                source_ranges,
            )
            if len(source_ranges) > 1:
                sort_mapped_records(source_ranges)

            expansion_ranges = MappedRecordVector(
                len(self._source_expansions),
                _TRANSLATED_RANGE_RECORD_FORMAT,
                spool_dir=self._spool_dir,
            )
            try:
                self._source_expansions.append_translated_ranges(
                    source_selection,
                    expansion_ranges,
                )
                if len(expansion_ranges) > 1:
                    sort_mapped_records(expansion_ranges)
                return LineRanges.from_ranges(
                    _merged_compact_translated_ranges(
                        source_ranges,
                        expansion_ranges,
                    )
                )
            finally:
                expansion_ranges.close()
        finally:
            source_ranges.close()

    def first_unmapped_source_line(
        self,
        selection: LineSelection | Iterable[int],
    ) -> int | None:
        self._require_open()
        return self._source_runs.first_unmapped_line(selection)

    def translate_working_line(self, line_number: int) -> int | None:
        self._require_open()
        return self._working_runs.translate_line(line_number)

    def translate_working_range(
        self,
        start_line: int,
        end_line: int,
    ) -> tuple[int, int] | None:
        """Translate a wholly preserved working-line range."""
        self._require_open()
        return self._working_runs.translate_range(start_line, end_line)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._source_runs.close()
        finally:
            try:
                self._working_runs.close()
            finally:
                self._source_expansions.close()
                self._closed = True

    def __enter__(self) -> BatchSourceLineage:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("batch source lineage is closed")


def _merged_compact_translated_ranges(
    source_ranges: MappedRecordVector,
    expansion_ranges: MappedRecordVector,
) -> Iterator[tuple[int, int]]:
    """Merge and coalesce two ordered mapped range streams."""
    source_index = 0
    expansion_index = 0
    current_start: int | None = None
    current_end: int | None = None

    while (
        source_index < len(source_ranges)
        or expansion_index < len(expansion_ranges)
    ):
        if (
            expansion_index >= len(expansion_ranges)
            or (
                source_index < len(source_ranges)
                and source_ranges[source_index]
                <= expansion_ranges[expansion_index]
            )
        ):
            start, end = source_ranges[source_index]
            source_index += 1
        else:
            start, end = expansion_ranges[expansion_index]
            expansion_index += 1

        if current_start is None or current_end is None:
            current_start = start
            current_end = end
        elif start <= current_end + 1:
            current_end = max(current_end, end)
        else:
            yield current_start, current_end
            current_start = start
            current_end = end

    if current_start is not None and current_end is not None:
        yield current_start, current_end
