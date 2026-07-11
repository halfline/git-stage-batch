"""Compact provenance run storage for realized entries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ...core.mapped_storage import ChunkedMappedRecordVector


_RUN_DEST_START = 0
_RUN_DEST_END = 1
_RUN_SOURCE_START = 2
_RUN_TARGET_START = 3
_RUN_FLAGS = 4
PROVENANCE_RUN_CLAIMED = 1
_PROVENANCE_CHUNK_CAPACITY = 8192


@dataclass(slots=True)
class ProvenanceRun:
    """Linear provenance over a half-open destination range."""

    dest_start: int
    dest_end: int
    source_start: int
    target_start: int
    flags: int

    @property
    def is_claimed(self) -> bool:
        return bool(self.flags & PROVENANCE_RUN_CLAIMED)

    def source_line_at(self, dest_index: int) -> int | None:
        if self.source_start == 0:
            return None
        return self.source_start + (dest_index - self.dest_start)

    def target_line_at(self, dest_index: int) -> int | None:
        if self.target_start == 0:
            return None
        return self.target_start + (dest_index - self.dest_start)

    def clipped(self, start: int, stop: int) -> ProvenanceRun | None:
        clipped_start = max(self.dest_start, start)
        clipped_end = min(self.dest_end, stop)
        if clipped_start >= clipped_end:
            return None

        offset = clipped_start - self.dest_start
        return ProvenanceRun(
            clipped_start,
            clipped_end,
            0 if self.source_start == 0 else self.source_start + offset,
            0 if self.target_start == 0 else self.target_start + offset,
            self.flags,
        )


def _run_source_is_contiguous(
    left: ProvenanceRun,
    right: ProvenanceRun,
) -> bool:
    if left.source_start == 0 or right.source_start == 0:
        return left.source_start == 0 and right.source_start == 0
    return right.source_start == left.source_start + (
        left.dest_end - left.dest_start
    )


def _run_target_is_contiguous(
    left: ProvenanceRun,
    right: ProvenanceRun,
) -> bool:
    if left.target_start == 0 or right.target_start == 0:
        return left.target_start == 0 and right.target_start == 0
    return right.target_start == left.target_start + (
        left.dest_end - left.dest_start
    )


def _runs_can_merge(left: ProvenanceRun, right: ProvenanceRun) -> bool:
    return (
        left.dest_end == right.dest_start
        and left.flags == right.flags
        and _run_source_is_contiguous(left, right)
        and _run_target_is_contiguous(left, right)
    )


def _run_from_record(record: tuple[int, ...]) -> ProvenanceRun:
    return ProvenanceRun(
        record[_RUN_DEST_START],
        record[_RUN_DEST_END],
        record[_RUN_SOURCE_START],
        record[_RUN_TARGET_START],
        record[_RUN_FLAGS],
    )


def stored_line_number(line_number: int | None) -> int:
    return 0 if line_number is None else line_number


def line_number_or_none(line_number: int) -> int | None:
    return None if line_number == 0 else line_number


class ProvenanceRunTable:
    """Append-only mapped provenance runs with one pending Python run."""

    def __init__(self) -> None:
        self._runs = ChunkedMappedRecordVector(
            record_format="QQQQQ",
            chunk_capacity=_PROVENANCE_CHUNK_CAPACITY,
        )
        self._pending_run: ProvenanceRun | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def flushed_run_count(self) -> int:
        self._require_open()
        return len(self._runs)

    @property
    def pending_run_count(self) -> int:
        self._require_open()
        return 1 if self._pending_run is not None else 0

    def __len__(self) -> int:
        self._require_open()
        return len(self._runs) + self.pending_run_count

    def append(
        self,
        dest_start: int,
        dest_end: int,
        *,
        source_start: int,
        target_start: int,
        flags: int,
    ) -> None:
        self._require_open()
        if dest_end < dest_start:
            raise ValueError("invalid provenance run")
        if dest_start == dest_end:
            return

        run = ProvenanceRun(
            dest_start,
            dest_end,
            source_start,
            target_start,
            flags,
        )
        if self._pending_run is None:
            self._pending_run = run
            return

        if _runs_can_merge(self._pending_run, run):
            self._pending_run.dest_end = run.dest_end
            return

        self._flush_pending()
        self._pending_run = run

    def run_at(self, dest_index: int) -> ProvenanceRun:
        self._require_open()
        pending = self._pending_run
        if (
            pending is not None
            and pending.dest_start <= dest_index < pending.dest_end
        ):
            return pending

        run = self._flushed_run_at(dest_index)
        if run is None:
            raise IndexError(dest_index)
        return run

    def runs(self, start: int, stop: int) -> Iterator[ProvenanceRun]:
        self._require_open()
        if stop <= start:
            return

        first_record = self._first_flushed_run_index_at_or_after(start)
        for record_index in range(first_record, len(self._runs)):
            run = _run_from_record(self._runs[record_index])
            if run.dest_start >= stop:
                break
            clipped = run.clipped(start, stop)
            if clipped is not None:
                yield clipped

        pending = self._pending_run
        if pending is not None:
            clipped = pending.clipped(start, stop)
            if clipped is not None:
                yield clipped

    def close(self) -> None:
        if self._closed:
            return
        self._pending_run = None
        self._runs.close()
        self._closed = True

    def __enter__(self) -> ProvenanceRunTable:
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
            pending.dest_start,
            pending.dest_end,
            pending.source_start,
            pending.target_start,
            pending.flags,
        ))
        self._pending_run = None

    def _flushed_run_at(self, dest_index: int) -> ProvenanceRun | None:
        low = 0
        high = len(self._runs)
        while low < high:
            mid = (low + high) // 2
            record = self._runs[mid]
            if dest_index < record[_RUN_DEST_START]:
                high = mid
            elif dest_index >= record[_RUN_DEST_END]:
                low = mid + 1
            else:
                return _run_from_record(record)
        return None

    def _first_flushed_run_index_at_or_after(self, start: int) -> int:
        low = 0
        high = len(self._runs)
        while low < high:
            mid = (low + high) // 2
            if self._runs[mid][_RUN_DEST_END] <= start:
                low = mid + 1
            else:
                high = mid
        return low

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("provenance run table is closed")
