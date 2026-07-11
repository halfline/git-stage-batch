"""Translate file-derived replacement runs inside live hunks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...core.line_selection import LineRanges
from ...core.models import LineEntry
from . import hunk_line_ranges as _hunk_line_ranges
from .absence_content import AbsenceContentBuilder
from .absence_claims import AbsenceClaim
from .claims import LineRangeBuilder
from .line_entries import (
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
    consumed_display_ids: set[int]


def translate_hunk_replacement_line_runs(
    *,
    hunk_lines: list[LineEntry],
    selected_display_ids: set[int],
    replacement_line_runs: Sequence[ReplacementLineRun],
    old_line_content: dict[int, bytes],
    hunk_content_view: Sequence[bytes],
) -> HunkReplacementTranslation:
    """Translate selected portions of file-derived replacement runs."""
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[ReplacementUnit] = []
    consumed_display_ids: set[int] = set()

    def add_replacement_unit(
        selected_old_ranges: Iterable[tuple[int, int]],
        selected_new_lines: Iterable[LineEntry],
        *,
        old_start: int,
        old_end: int,
        origin: ReplacementUnitOrigin | None = None,
    ) -> None:
        deletion_anchor: int | None = None
        old_line_seen = False
        selected_source_lines = LineRangeBuilder()
        consumed_ids: list[int] = []
        with AbsenceContentBuilder() as builder:
            for range_start, range_stop in selected_old_ranges:
                if not old_line_seen:
                    deletion_anchor = hunk_lines[range_start].source_line
                    old_line_seen = True
                builder.append_line_range(
                    hunk_content_view,
                    range_start,
                    range_stop,
                )
                for index in range(range_start, range_stop):
                    old_line = hunk_lines[index]
                    if old_line.id is not None:
                        consumed_ids.append(old_line.id)

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
                consumed_ids.append(new_line.id)
            baseline_reference = baseline_reference_for_presence_line(new_line)
            if baseline_reference is not None:
                presence_baseline_references[new_line.source_line] = (
                    baseline_reference
                )

        absence_claims.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=content_lines,
                baseline_reference=baseline_reference_for_old_line_range(
                    old_start,
                    old_end,
                    old_line_content,
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
        consumed_display_ids.update(consumed_ids)

    old_cursor = 0
    new_cursor = 0

    for replacement_run in replacement_line_runs:
        replacement_origin = replacement_unit_origin_for_line_run(
            replacement_run,
            old_line_content,
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
                    if old_line.old_line_number is None:
                        continue
                    add_replacement_unit(
                        ((old_index, old_index + 1),),
                        (new_line,),
                        old_start=old_line.old_line_number,
                        old_end=old_line.old_line_number,
                        origin=replacement_origin,
                    )
            continue

        if old_scan.fully_selected and new_scan.fully_selected:
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
                origin=replacement_origin,
            )

    return HunkReplacementTranslation(
        claimed_source_lines=claimed_source_lines.finish(),
        presence_baseline_references=presence_baseline_references,
        absence_claims=absence_claims,
        replacement_units=replacement_units,
        consumed_display_ids=consumed_display_ids,
    )
