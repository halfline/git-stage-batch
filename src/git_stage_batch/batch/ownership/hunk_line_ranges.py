"""Line-range scanning helpers for rendered live hunks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ...core.models import LineEntry


@dataclass(frozen=True)
class HunkLineRangeScan:
    start: int
    end: int
    start_index: int
    stop_index: int
    count: int
    selected_count: int

    @property
    def complete(self) -> bool:
        return self.count == self.end - self.start + 1

    @property
    def fully_selected(self) -> bool:
        return self.complete and self.selected_count == self.count


def scan_hunk_line_range(
    hunk_lines: list[LineEntry],
    cursor: int,
    *,
    kind: str,
    line_number_attr: str,
    start: int,
    end: int,
    selected_display_ids: set[int],
) -> HunkLineRangeScan:
    index = cursor
    start_index = cursor
    count = 0
    selected_count = 0
    found_first = False

    while index < len(hunk_lines):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if line_number is not None and line_number > end:
            break
        if line.kind == kind and line_number is not None:
            if line_number < start:
                index += 1
                continue
            if line_number > end:
                break
            if not found_first:
                start_index = index
                found_first = True
            count += 1
            if line.id is not None and line.id in selected_display_ids:
                selected_count += 1
        index += 1

    return HunkLineRangeScan(
        start=start,
        end=end,
        start_index=start_index,
        stop_index=index,
        count=count,
        selected_count=selected_count,
    )


def hunk_line_indexes_in_range(
    hunk_lines: list[LineEntry],
    scan: HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[int]:
    for index in range(scan.start_index, scan.stop_index):
        line = hunk_lines[index]
        line_number = getattr(line, line_number_attr)
        if (
            line.kind == kind
            and line_number is not None
            and scan.start <= line_number <= scan.end
        ):
            yield index


def hunk_line_index_ranges_in_range(
    hunk_lines: list[LineEntry],
    scan: HunkLineRangeScan,
    *,
    kind: str,
    line_number_attr: str,
) -> Iterable[tuple[int, int]]:
    pending_start: int | None = None
    pending_stop: int | None = None

    for index in hunk_line_indexes_in_range(
        hunk_lines,
        scan,
        kind=kind,
        line_number_attr=line_number_attr,
    ):
        if pending_stop == index:
            pending_stop = index + 1
            continue

        if pending_start is not None and pending_stop is not None:
            yield pending_start, pending_stop
        pending_start = index
        pending_stop = index + 1

    if pending_start is not None and pending_stop is not None:
        yield pending_start, pending_stop
