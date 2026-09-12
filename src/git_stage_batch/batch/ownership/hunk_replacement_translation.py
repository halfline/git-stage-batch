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


def translate_hunk_replacement_line_runs(
    *,
    hunk_lines: Sequence[LineEntry],
    selected_display_ids: Collection[int],
    replacement_line_runs: Iterable[ReplacementLineRun],
    old_line_content: Mapping[int, bytes],
    hunk_content_view: Sequence[bytes],
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
            replacement_unit_origin_for_line_run(replacement_run, old_line_content)
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
