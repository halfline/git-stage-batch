"""Translate file-derived replacement runs inside live hunks."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass

from ...core.line_selection import LineRanges
from ...core.models import LineEntry
from . import hunk_line_ranges as _hunk_line_ranges
from .absence_content import AbsenceContentBuilder
from .absence_claims import AbsenceClaim
from .claims import LineRangeBuilder
from .line_entries import (
    baseline_reference_for_file_line_range,
    baseline_reference_for_old_line_range,
    baseline_reference_for_presence_line,
    replacement_unit_origin_for_line_run,
)
from .references import BaselineReference
from .replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from .replacement_line_runs import ReplacementLineRun


@dataclass
class HunkReplacementTranslation:
    claimed_source_lines: LineRanges
    presence_baseline_references: dict[int, BaselineReference]
    absence_claims: list[AbsenceClaim]
    replacement_units: list[ReplacementUnit]
    consumed_display_ids: LineRanges


def _close_replacement_run_iterator(
    iterator: Iterator[ReplacementLineRun],
) -> None:
    close = getattr(iterator, "close", None)
    if close is not None:
        close()


def translate_hunk_replacement_line_runs(
    *,
    hunk_lines: list[LineEntry],
    selected_display_ids: Collection[int],
    replacement_line_runs: Iterable[ReplacementLineRun],
    old_line_content: dict[int, bytes],
    hunk_content_view: Sequence[bytes],
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
) -> HunkReplacementTranslation:
    """Translate selected portions of file-derived replacement runs."""
    if (
        replacement_origin_line_runs is None
    ) != (
        replacement_origin_source_lines is None
    ):
        raise ValueError(
            "replacement origin runs and source lines must be provided together"
        )

    replacement_run_iterator = iter(replacement_line_runs)
    try:
        origin_run_iterator = iter(
            ()
            if replacement_origin_line_runs is None
            else replacement_origin_line_runs
        )
        try:
            return _translate_hunk_replacement_line_runs(
                hunk_lines=hunk_lines,
                selected_display_ids=selected_display_ids,
                replacement_run_iterator=replacement_run_iterator,
                old_line_content=old_line_content,
                hunk_content_view=hunk_content_view,
                origin_run_iterator=origin_run_iterator,
                replacement_origin_source_lines=replacement_origin_source_lines,
            )
        finally:
            _close_replacement_run_iterator(origin_run_iterator)
    finally:
        _close_replacement_run_iterator(replacement_run_iterator)


def _translate_hunk_replacement_line_runs(
    *,
    hunk_lines: list[LineEntry],
    selected_display_ids: Collection[int],
    replacement_run_iterator: Iterator[ReplacementLineRun],
    old_line_content: dict[int, bytes],
    hunk_content_view: Sequence[bytes],
    origin_run_iterator: Iterator[ReplacementLineRun],
    replacement_origin_source_lines: Sequence[bytes] | None,
) -> HunkReplacementTranslation:
    """Translate replacement runs whose iterator lifetimes are caller-owned."""
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []
    consumed_old_display_ids = LineRangeBuilder()
    consumed_new_display_ids = LineRangeBuilder()

    def add_replacement_unit(
        selected_old_ranges: Iterable[tuple[int, int]],
        selected_new_lines: Iterable[LineEntry],
        *,
        old_start: int,
        old_end: int,
        origin: ReplacementUnitOrigin | None = None,
        origin_old_start: int | None = None,
        origin_old_end: int | None = None,
    ) -> None:
        deletion_anchor: int | None = None
        old_line_seen = False
        selected_source_lines = LineRangeBuilder()
        use_origin_content = (
            origin_old_start is not None
            and origin_old_end is not None
            and replacement_origin_source_lines is not None
        )
        with AbsenceContentBuilder() as builder:
            for range_start, range_stop in selected_old_ranges:
                if not old_line_seen:
                    deletion_anchor = hunk_lines[range_start].source_line
                    old_line_seen = True
                if not use_origin_content:
                    builder.append_line_range(
                        hunk_content_view,
                        range_start,
                        range_stop,
                    )
                for index in range(range_start, range_stop):
                    old_line = hunk_lines[index]
                    if old_line.id is not None:
                        consumed_old_display_ids.add_line(old_line.id)

            if use_origin_content:
                assert origin_old_start is not None
                assert origin_old_end is not None
                assert replacement_origin_source_lines is not None
                builder.append_line_range(
                    replacement_origin_source_lines,
                    origin_old_start - 1,
                    origin_old_end,
                )
            content_lines = builder.finish()

        for new_line in selected_new_lines:
            if new_line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={new_line.kind!r}, text={new_line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            claimed_source_lines.add_line(new_line.source_line)
            selected_source_lines.add_line(new_line.source_line)
            if new_line.id is not None:
                consumed_new_display_ids.add_line(new_line.id)
            baseline_reference = baseline_reference_for_presence_line(new_line)
            if baseline_reference is not None:
                presence_baseline_references[new_line.source_line] = (
                    baseline_reference
                )

        absence_claims.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=content_lines,
                baseline_reference=(
                    baseline_reference_for_file_line_range(
                        origin_old_start,
                        origin_old_end,
                        replacement_origin_source_lines,
                    )
                    if (
                        origin_old_start is not None
                        and origin_old_end is not None
                        and replacement_origin_source_lines is not None
                    )
                    else baseline_reference_for_old_line_range(
                        old_start,
                        old_end,
                        old_line_content,
                    )
                ),
            )
        )
        replacement_units.append(
            ReplacementUnit(
                presence_lines=selected_source_lines.finish().to_range_strings(),
                deletion_indices=[len(absence_claims) - 1],
                origin=origin,
            )
        )

    old_cursor = 0
    new_cursor = 0
    next_origin_run = next(origin_run_iterator, None)
    cached_origin_run: ReplacementLineRun | None = None
    cached_origin: ReplacementUnitOrigin | None = None

    def origin_projection_for_new_range(
        new_start: int,
        new_end: int,
    ) -> tuple[ReplacementUnitOrigin, int, int] | None:
        """Project a displayed replacement range through live HEAD."""
        nonlocal next_origin_run
        nonlocal cached_origin_run
        nonlocal cached_origin

        if replacement_origin_source_lines is None:
            return None

        while (
            next_origin_run is not None
            and next_origin_run.new_end < new_start
        ):
            next_origin_run = next(origin_run_iterator, None)
        origin_run = next_origin_run
        if (
            origin_run is None
            or origin_run.new_start > new_start
            or new_end > origin_run.new_end
        ):
            return None

        if (
            new_start == origin_run.new_start
            and new_end == origin_run.new_end
        ):
            origin_old_start = origin_run.old_start
            origin_old_end = origin_run.old_end
        else:
            origin_old_count = origin_run.old_end - origin_run.old_start + 1
            origin_new_count = origin_run.new_end - origin_run.new_start + 1
            if origin_old_count != origin_new_count:
                return None
            origin_old_start = (
                origin_run.old_start + new_start - origin_run.new_start
            )
            origin_old_end = origin_old_start + new_end - new_start

        if cached_origin_run != origin_run:
            cached_origin_run = origin_run
            cached_origin = replacement_unit_origin_for_line_run(
                origin_run,
                old_file_lines=replacement_origin_source_lines,
            )
        assert cached_origin is not None
        return cached_origin, origin_old_start, origin_old_end

    for replacement_run in replacement_run_iterator:
        legacy_replacement_origin = (
            replacement_unit_origin_for_line_run(
                replacement_run,
                old_line_content,
            )
            if replacement_origin_source_lines is None
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

        if not old_scan.complete or not new_scan.complete:
            continue

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
                old_selected = (
                    old_line.id is not None
                    and old_line.id in selected_display_ids
                )
                new_selected = (
                    new_line.id is not None
                    and new_line.id in selected_display_ids
                )
                if old_selected and new_selected:
                    if (
                        old_line.old_line_number is None
                        or new_line.new_line_number is None
                    ):
                        continue
                    origin_projection = origin_projection_for_new_range(
                        new_line.new_line_number,
                        new_line.new_line_number,
                    )
                    add_replacement_unit(
                        ((old_index, old_index + 1),),
                        (new_line,),
                        old_start=old_line.old_line_number,
                        old_end=old_line.old_line_number,
                        origin=(
                            origin_projection[0]
                            if origin_projection is not None
                            else legacy_replacement_origin
                        ),
                        origin_old_start=(
                            origin_projection[1]
                            if origin_projection is not None
                            else None
                        ),
                        origin_old_end=(
                            origin_projection[2]
                            if origin_projection is not None
                            else None
                        ),
                    )
            continue

        if old_scan.fully_selected and new_scan.fully_selected:
            origin_projection = origin_projection_for_new_range(
                replacement_run.new_start,
                replacement_run.new_end,
            )
            add_replacement_unit(
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
                old_start=replacement_run.old_start,
                old_end=replacement_run.old_end,
                origin=(
                    origin_projection[0]
                    if origin_projection is not None
                    else legacy_replacement_origin
                ),
                origin_old_start=(
                    origin_projection[1]
                    if origin_projection is not None
                    else None
                ),
                origin_old_end=(
                    origin_projection[2]
                    if origin_projection is not None
                    else None
                ),
            )

    consumed_old_ids = consumed_old_display_ids.finish()
    consumed_new_ids = consumed_new_display_ids.finish()
    return HunkReplacementTranslation(
        claimed_source_lines=claimed_source_lines.finish(),
        presence_baseline_references=presence_baseline_references,
        absence_claims=absence_claims,
        replacement_units=replacement_units,
        consumed_display_ids=LineRanges.from_ranges(
            range_pair
            for consumed_ids in (consumed_old_ids, consumed_new_ids)
            for range_pair in consumed_ids.ranges()
        ),
    )
