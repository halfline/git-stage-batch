"""Stream the selected portions of one file-derived replacement run."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from ...core.models import LineEntry
from ...core.repeated_context_replacement import (
    find_repeated_context_suffix_replacement,
)
from . import hunk_line_ranges as _hunk_line_ranges
from .replacement_line_runs import ReplacementLineRun


@dataclass(frozen=True, slots=True)
class SelectedReplacementRun:
    """Borrowed row ranges for immediate ownership assembly."""

    old_ranges: Iterable[tuple[int, int]]
    new_lines: Iterable[LineEntry]
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    align_suffix: bool = False


def _repeated_context_suffix_selection(
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    old_scan: _hunk_line_ranges.HunkLineRangeScan,
    new_scan: _hunk_line_ranges.HunkLineRangeScan,
    replacement_run: ReplacementLineRun,
) -> SelectedReplacementRun | None:
    """Find a selected raw suffix aligned to a semantic run's end."""
    scan_start = min(old_scan.start_index, new_scan.start_index)
    scan_stop = max(old_scan.stop_index, new_scan.stop_index)
    line_index = scan_start
    while line_index < scan_stop:
        if hunk_lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue

        run_start = line_index
        while line_index < scan_stop and hunk_lines[line_index].kind in ("+", "-"):
            line_index += 1
        run_end = line_index
        suffix = find_repeated_context_suffix_replacement(
            hunk_lines,
            selected_display_ids,
            run_start,
            run_end,
        )
        if suffix is None:
            continue

        old_start = hunk_lines[run_start].old_line_number
        old_end = hunk_lines[suffix.first_addition - 1].old_line_number
        new_start = hunk_lines[suffix.selected_suffix_start].new_line_number
        new_end = hunk_lines[run_end - 1].new_line_number
        if (
            old_start is None
            or old_end is None
            or new_start is None
            or new_end is None
            or old_end != replacement_run.old_end
            or new_end != replacement_run.new_end
            or old_end - old_start != new_end - new_start
        ):
            continue
        return SelectedReplacementRun(
            ((run_start, suffix.first_addition),),
            (
                hunk_lines[index]
                for index in range(suffix.selected_suffix_start, run_end)
            ),
            old_start,
            old_end,
            new_start,
            new_end,
            align_suffix=True,
        )
    return None


def selected_replacement_runs(
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    old_scan: _hunk_line_ranges.HunkLineRangeScan,
    new_scan: _hunk_line_ranges.HunkLineRangeScan,
    replacement_run: ReplacementLineRun,
) -> Iterator[SelectedReplacementRun]:
    """Yield suffix, paired, or complete selections without materializing rows."""
    if not old_scan.complete or not new_scan.complete:
        suffix = _repeated_context_suffix_selection(
            hunk_lines,
            selected_display_ids,
            old_scan,
            new_scan,
            replacement_run,
        )
        if suffix is not None:
            yield suffix
        return
    if old_scan.count == new_scan.count:
        old_indexes = _hunk_line_ranges.hunk_line_indexes_in_range(
            hunk_lines,
            old_scan,
            kind="-",
            line_number_attr="old_line_number",
        )
        new_indexes = _hunk_line_ranges.hunk_line_indexes_in_range(
            hunk_lines,
            new_scan,
            kind="+",
            line_number_attr="new_line_number",
        )
        for old_index, new_index in zip(old_indexes, new_indexes):
            old_line = hunk_lines[old_index]
            new_line = hunk_lines[new_index]
            if not (
                old_line.id is not None
                and old_line.id in selected_display_ids
                and new_line.id is not None
                and new_line.id in selected_display_ids
            ):
                continue
            if old_line.old_line_number is None or new_line.new_line_number is None:
                continue
            yield SelectedReplacementRun(
                ((old_index, old_index + 1),),
                (new_line,),
                old_line.old_line_number,
                old_line.old_line_number,
                new_line.new_line_number,
                new_line.new_line_number,
            )
        return
    if old_scan.fully_selected and new_scan.fully_selected:
        yield SelectedReplacementRun(
            _hunk_line_ranges.hunk_line_index_ranges_in_range(
                hunk_lines,
                old_scan,
                kind="-",
                line_number_attr="old_line_number",
            ),
            (
                hunk_lines[index]
                for index in _hunk_line_ranges.hunk_line_indexes_in_range(
                    hunk_lines,
                    new_scan,
                    kind="+",
                    line_number_attr="new_line_number",
                )
            ),
            replacement_run.old_start,
            replacement_run.old_end,
            replacement_run.new_start,
            replacement_run.new_end,
        )
