"""Compact line lineage for refreshed batch sources."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..core.line_selection import LineRanges, LineSelection
from ..utils.mapped_storage import ChunkedMappedRecordVector


_LINEAGE_RECORD_FORMAT = "QQQ"
_LINEAGE_CHUNK_CAPACITY = 8192
_LINEAGE_OLD_START = 0
_LINEAGE_OLD_END = 1
_LINEAGE_NEW_START = 2


@dataclass(frozen=True, slots=True)
class _LineageRun:
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


def _lineage_run_from_record(record: tuple[int, ...]) -> _LineageRun:
    return _LineageRun(
        record[_LINEAGE_OLD_START],
        record[_LINEAGE_OLD_END],
        record[_LINEAGE_NEW_START],
    )


def _selection_ranges(
    selection: LineSelection | Iterable[int],
) -> tuple[tuple[int, int], ...]:
    ranges = getattr(selection, "ranges", None)
    if ranges is not None:
        return ranges()
    return LineRanges.from_lines(selection).ranges()


def _lineage_runs_can_merge(left: _LineageRun, right: _LineageRun) -> bool:
    return (
        right.old_start == left.old_end + 1
        and right.new_start == left.new_end + 1
    )


class _LineageRunTable:
    """Append-only mapped lineage runs with one pending Python run."""

    def __init__(
        self,
        runs: Iterable[_LineageRun] = (),
        *,
        spool_dir: str | Path | None = None,
    ) -> None:
        self._runs = ChunkedMappedRecordVector(
            record_format=_LINEAGE_RECORD_FORMAT,
            chunk_capacity=_LINEAGE_CHUNK_CAPACITY,
            spool_dir=spool_dir,
        )
        self._pending_run: _LineageRun | None = None
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

    def append(self, run: _LineageRun) -> None:
        self._require_open()
        pending = self._pending_run
        if pending is None:
            self._pending_run = run
            return

        if run.old_start <= pending.old_end:
            raise ValueError("lineage runs must not overlap")

        if _lineage_runs_can_merge(pending, run):
            self._pending_run = _LineageRun(
                old_start=pending.old_start,
                old_end=run.old_end,
                new_start=pending.new_start,
            )
            return

        self._flush_pending()
        self._pending_run = run

    def run_at(self, old_line: int) -> _LineageRun | None:
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

    def runs(self) -> Iterator[_LineageRun]:
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

    def translate_selection(
        self,
        selection: LineSelection | Iterable[int],
    ) -> LineRanges:
        self._require_open()
        translated_ranges: list[tuple[int, int]] = []
        run_index = 0

        for selected_start, selected_end in _selection_ranges(selection):
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
                    translated_ranges.append(run.translate_range(old_start, old_end))
                if run.old_end >= selected_end:
                    break
                scan_index += 1

        return LineRanges.from_ranges(translated_ranges)

    def first_unmapped_line(
        self,
        selection: LineSelection | Iterable[int],
    ) -> int | None:
        self._require_open()
        run_index = 0

        for selected_start, selected_end in _selection_ranges(selection):
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

    def __exit__(self, exc_type, exc, traceback) -> None:
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

    def _run_at_index(self, index: int) -> _LineageRun:
        flushed_count = len(self._runs)
        if 0 <= index < flushed_count:
            return _lineage_run_from_record(self._runs[index])
        if index == flushed_count and self._pending_run is not None:
            return self._pending_run
        raise IndexError(index)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("lineage run table is closed")


class _BatchSourceLineage:
    """Lineage from old source and working lines to refreshed source lines."""

    def __init__(
        self,
        source_runs: Iterable[_LineageRun] = (),
        working_runs: Iterable[_LineageRun] = (),
        *,
        spool_dir: str | Path | None = None,
    ) -> None:
        self._source_runs = _LineageRunTable(
            source_runs,
            spool_dir=spool_dir,
        )
        self._working_runs = _LineageRunTable(
            working_runs,
            spool_dir=spool_dir,
        )
        self._closed = False

    @classmethod
    def from_runs(
        cls,
        *,
        source_runs: Iterable[_LineageRun] = (),
        working_runs: Iterable[_LineageRun] = (),
        spool_dir: str | Path | None = None,
    ) -> _BatchSourceLineage:
        return cls(source_runs, working_runs, spool_dir=spool_dir)

    @property
    def byte_count(self) -> int:
        if self._closed:
            return 0
        return self._source_runs.byte_count + self._working_runs.byte_count

    @property
    def closed(self) -> bool:
        return self._closed

    def source_runs(self) -> Iterator[_LineageRun]:
        self._require_open()
        return self._source_runs.runs()

    def working_runs(self) -> Iterator[_LineageRun]:
        self._require_open()
        return self._working_runs.runs()

    def append_source_run(self, run: _LineageRun) -> None:
        self._require_open()
        self._source_runs.append(run)

    def append_working_run(self, run: _LineageRun) -> None:
        self._require_open()
        self._working_runs.append(run)

    def translate_source_line(self, line_number: int) -> int | None:
        self._require_open()
        return self._source_runs.translate_line(line_number)

    def translate_source_selection(
        self,
        selection: LineSelection | Iterable[int],
    ) -> LineRanges:
        self._require_open()
        return self._source_runs.translate_selection(selection)

    def first_unmapped_source_line(
        self,
        selection: LineSelection | Iterable[int],
    ) -> int | None:
        self._require_open()
        return self._source_runs.first_unmapped_line(selection)

    def translate_working_line(self, line_number: int) -> int | None:
        self._require_open()
        return self._working_runs.translate_line(line_number)

    def translate_working_selection(
        self,
        selection: LineSelection | Iterable[int],
    ) -> LineRanges:
        self._require_open()
        return self._working_runs.translate_selection(selection)

    def close(self) -> None:
        if self._closed:
            return
        self._source_runs.close()
        self._working_runs.close()
        self._closed = True

    def __enter__(self) -> _BatchSourceLineage:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("batch source lineage is closed")
