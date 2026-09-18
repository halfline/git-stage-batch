"""Translate file-derived replacement runs inside live hunks."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import TypeVar
from ...core.models import LineEntry
from . import hunk_line_ranges as _hunk_line_ranges
from .hunk_replacement_assembly import (
    HunkReplacementBuilder,
    HunkReplacementTranslation,
)
from .hunk_replacement_selection import selected_replacement_runs
from .replacement_origin_cursor import ReplacementOriginCursor
from .line_entries import replacement_unit_origin_for_line_run
from .replacement_line_runs import ReplacementLineRun
from .replacement_units import ReplacementUnitOrigin
from .replacement_origins import (
    NoReplacementOrigin,
    ProjectedReplacementOrigin,
    ReplacementOrigin,
    ReplacementOriginSourceProjection,
)
from ..source.projection import SourceCoordinateProjection

OriginSourceSpace = TypeVar("OriginSourceSpace")


def _close_replacement_run_iterator(
    iterator: Iterator[ReplacementLineRun],
) -> None:
    close = getattr(iterator, "close", None)
    if close is not None:
        close()


def _translate_hidden_baseline_replacement(
    *,
    builder: HunkReplacementBuilder,
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    new_scan: _hunk_line_ranges.HunkLineRangeScan,
    replacement_run: ReplacementLineRun,
    baseline_lines: Sequence[bytes],
    origins: ReplacementOriginCursor[OriginSourceSpace],
    legacy_origin: ReplacementUnitOrigin | None,
) -> bool:
    """Translate selected new rows whose staged old rows are not displayed."""
    old_count = replacement_run.old_end - replacement_run.old_start + 1
    new_count = replacement_run.new_end - replacement_run.new_start + 1

    def add_selected_run(
        first_index: int,
        stop_index: int,
        new_start: int,
        new_end: int,
        old_start: int,
        old_end: int,
    ) -> None:
        def selected_new_lines() -> Iterator[LineEntry]:
            for index in range(first_index, stop_index):
                line = hunk_lines[index]
                line_number = line.new_line_number
                if (
                    line.kind == "+"
                    and line_number is not None
                    and new_start <= line_number <= new_end
                ):
                    yield line

        origin = origins.project(
            new_start,
            new_end,
            replacement_run=replacement_run,
            align_suffix=False,
        )
        builder.add_baseline_replacement_unit(
            selected_new_lines(),
            baseline_lines,
            old_start=old_start,
            old_end=old_end,
            origin=origin[0] if origin is not None else legacy_origin,
        )

    if old_count != new_count:
        if not new_scan.fully_selected:
            return False
        add_selected_run(
            new_scan.start_index,
            new_scan.stop_index,
            replacement_run.new_start,
            replacement_run.new_end,
            replacement_run.old_start,
            replacement_run.old_end,
        )
        return True

    selected_start_index: int | None = None
    selected_start_line: int | None = None
    selected_stop_index: int | None = None
    selected_end_line: int | None = None

    def finish_selected_run() -> None:
        nonlocal selected_start_index
        nonlocal selected_start_line
        nonlocal selected_stop_index
        nonlocal selected_end_line
        if selected_start_index is None:
            return
        assert selected_start_line is not None
        assert selected_stop_index is not None
        assert selected_end_line is not None
        old_start = (
            replacement_run.old_start
            + selected_start_line
            - replacement_run.new_start
        )
        old_end = old_start + selected_end_line - selected_start_line
        add_selected_run(
            selected_start_index,
            selected_stop_index,
            selected_start_line,
            selected_end_line,
            old_start,
            old_end,
        )
        selected_start_index = None
        selected_start_line = None
        selected_stop_index = None
        selected_end_line = None

    for index in _hunk_line_ranges.hunk_line_indexes_in_range(
        hunk_lines,
        new_scan,
        kind="+",
        line_number_attr="new_line_number",
    ):
        line = hunk_lines[index]
        assert line.new_line_number is not None
        if line.id is None or line.id not in selected_display_ids:
            finish_selected_run()
            continue
        if selected_start_index is None:
            selected_start_index = index
            selected_start_line = line.new_line_number
        selected_stop_index = index + 1
        selected_end_line = line.new_line_number
    finish_selected_run()
    return new_scan.selected_count > 0


def translate_hunk_replacement_line_runs(
    *,
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    replacement_line_runs: Iterable[ReplacementLineRun],
    old_line_content: Mapping[int, bytes],
    hunk_content_view: Sequence[bytes],
    baseline_lines: Sequence[bytes] | None = None,
    replacement_origin: ReplacementOrigin = NoReplacementOrigin(),
    source_projection: SourceCoordinateProjection | None = None,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[OriginSourceSpace] | None
    ) = None,
) -> HunkReplacementTranslation:
    """Translate selected portions of file-derived replacement runs."""
    replacement_run_iterator = iter(replacement_line_runs)
    try:
        origin_run_iterator = iter(
            replacement_origin.runs
            if isinstance(replacement_origin, ProjectedReplacementOrigin)
            else ()
        )
        try:
            return _translate_hunk_replacement_line_runs(
                hunk_lines=hunk_lines,
                selected_display_ids=selected_display_ids,
                replacement_run_iterator=replacement_run_iterator,
                old_line_content=old_line_content,
                hunk_content_view=hunk_content_view,
                baseline_lines=baseline_lines,
                origin_run_iterator=origin_run_iterator,
                replacement_origin=replacement_origin,
                source_projection=source_projection,
                replacement_origin_source_projection=(
                    replacement_origin_source_projection
                ),
            )
        finally:
            _close_replacement_run_iterator(origin_run_iterator)
    finally:
        _close_replacement_run_iterator(replacement_run_iterator)


def _translate_hunk_replacement_line_runs(
    *,
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    replacement_run_iterator: Iterator[ReplacementLineRun],
    old_line_content: Mapping[int, bytes],
    hunk_content_view: Sequence[bytes],
    baseline_lines: Sequence[bytes] | None,
    origin_run_iterator: Iterator[ReplacementLineRun],
    replacement_origin: ReplacementOrigin,
    source_projection: SourceCoordinateProjection | None,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[OriginSourceSpace] | None
    ),
) -> HunkReplacementTranslation:
    """Translate replacement runs whose iterator lifetimes are caller-owned."""
    origins = ReplacementOriginCursor(
        replacement_origin, origin_run_iterator, replacement_origin_source_projection
    )
    builder = HunkReplacementBuilder(
        hunk_lines,
        old_line_content,
        hunk_content_view,
        source_projection,
        origins.replacement_origin_source_lines,
    )
    old_cursor = 0
    new_cursor = 0
    for replacement_run in replacement_run_iterator:
        legacy_origin = (
            replacement_unit_origin_for_line_run(
                replacement_run,
                old_line_content if baseline_lines is None else None,
                old_file_lines=baseline_lines,
            )
            if origins.replacement_origin_source_lines is None
            else None
        )
        old_scan = _hunk_line_ranges.scan_hunk_line_range(
            hunk_lines,
            old_cursor,
            kind="-",
            line_number_attr="old_line_number",
            start=replacement_run.old_start,
            end=replacement_run.old_end,
            selected_display_ids=selected_display_ids,
        )
        new_scan = _hunk_line_ranges.scan_hunk_line_range(
            hunk_lines,
            new_cursor,
            kind="+",
            line_number_attr="new_line_number",
            start=replacement_run.new_start,
            end=replacement_run.new_end,
            selected_display_ids=selected_display_ids,
        )
        old_cursor = old_scan.stop_index
        new_cursor = new_scan.stop_index

        if (
            baseline_lines is not None
            and old_scan.count == 0
            and new_scan.complete
            and _translate_hidden_baseline_replacement(
                builder=builder,
                hunk_lines=hunk_lines,
                selected_display_ids=selected_display_ids,
                new_scan=new_scan,
                replacement_run=replacement_run,
                baseline_lines=baseline_lines,
                origins=origins,
                legacy_origin=legacy_origin,
            )
        ):
            continue

        for selected in selected_replacement_runs(
            hunk_lines,
            selected_display_ids,
            old_scan,
            new_scan,
            replacement_run,
        ):
            origin = origins.project(
                selected.new_start,
                selected.new_end,
                replacement_run=replacement_run,
                align_suffix=selected.align_suffix,
            )
            builder.add_replacement_unit(
                selected.old_ranges,
                selected.new_lines,
                old_start=selected.old_start,
                old_end=selected.old_end,
                origin=origin[0] if origin is not None else legacy_origin,
                origin_old_start=origin[1] if origin is not None else None,
                origin_old_end=origin[2] if origin is not None else None,
            )
    return builder.finish()
