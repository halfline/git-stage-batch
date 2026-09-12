"""Assemble ownership from selected old ranges and streamed new rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.models import LineEntry
from .absence_content import AbsenceContentBuilder
from .absence_claims import AbsenceClaim
from .line_entries import (
    baseline_reference_for_file_line_range,
    baseline_reference_for_old_line_range,
    baseline_reference_for_presence_line,
)
from .references import BaselineReference
from .replacement_units import ReplacementUnit, ReplacementUnitOrigin
from ..source.projection import SourceCoordinateProjection


@dataclass
class HunkReplacementTranslation:
    claimed_source_lines: LineRanges
    presence_baseline_references: dict[int, BaselineReference]
    absence_claims: list[AbsenceClaim]
    replacement_units: list[ReplacementUnit]
    consumed_display_ids: LineRanges


class HunkReplacementBuilder:
    """Accumulate ownership while preserving separate monotonic ID ranges."""

    def __init__(
        self,
        hunk_lines: Sequence[LineEntry],
        old_line_content: Mapping[int, bytes],
        hunk_content_view: Sequence[bytes],
        source_projection: SourceCoordinateProjection | None,
        replacement_origin_source_lines: Sequence[bytes] | None,
    ) -> None:
        self.hunk_lines = hunk_lines
        self.old_line_content = old_line_content
        self.hunk_content_view = hunk_content_view
        self.source_projection = source_projection
        self.replacement_origin_source_lines = replacement_origin_source_lines
        self.claimed_source_lines = LineRangeBuilder()
        self.presence_baseline_references: dict[int, BaselineReference] = {}
        self.absence_claims: list[AbsenceClaim] = []
        self.replacement_units: list[ReplacementUnit] = []
        self.consumed_old_display_ids = LineRangeBuilder()
        self.consumed_new_display_ids = LineRangeBuilder()

    def source_line_for(self, line: LineEntry) -> int | None:
        if self.source_projection is None:
            return line.source_line
        return self.source_projection.source_line_for(line)

    def add_replacement_unit(
        self,
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
            and self.replacement_origin_source_lines is not None
        )
        with AbsenceContentBuilder() as builder:
            for range_start, range_stop in selected_old_ranges:
                if not old_line_seen:
                    deletion_anchor = self.source_line_for(self.hunk_lines[range_start])
                    old_line_seen = True
                if not use_origin_content:
                    builder.append_line_range(
                        self.hunk_content_view,
                        range_start,
                        range_stop,
                    )
                for index in range(range_start, range_stop):
                    old_line = self.hunk_lines[index]
                    if old_line.id is not None:
                        self.consumed_old_display_ids.add_line(old_line.id)

            if use_origin_content:
                assert origin_old_start is not None
                assert origin_old_end is not None
                assert self.replacement_origin_source_lines is not None
                builder.append_line_range(
                    self.replacement_origin_source_lines,
                    origin_old_start - 1,
                    origin_old_end,
                )
            content_lines = builder.finish()

        for new_line in selected_new_lines:
            source_line = self.source_line_for(new_line)
            if source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={new_line.kind!r}, text={new_line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            self.claimed_source_lines.add_line(source_line)
            selected_source_lines.add_line(source_line)
            if new_line.id is not None:
                self.consumed_new_display_ids.add_line(new_line.id)
            baseline_reference = baseline_reference_for_presence_line(new_line)
            if baseline_reference is not None:
                self.presence_baseline_references[source_line] = baseline_reference

        self.absence_claims.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=content_lines,
                baseline_reference=(
                    baseline_reference_for_file_line_range(
                        origin_old_start,
                        origin_old_end,
                        self.replacement_origin_source_lines,
                    )
                    if (
                        origin_old_start is not None
                        and origin_old_end is not None
                        and self.replacement_origin_source_lines is not None
                    )
                    else baseline_reference_for_old_line_range(
                        old_start,
                        old_end,
                        self.old_line_content,
                    )
                ),
            )
        )
        self.replacement_units.append(
            ReplacementUnit(
                presence_lines=selected_source_lines.finish().to_range_strings(),
                deletion_indices=[len(self.absence_claims) - 1],
                origin=origin,
            )
        )

    def finish(self) -> HunkReplacementTranslation:
        """Return the accumulated ownership without copying its claims."""
        consumed_old_ids = self.consumed_old_display_ids.finish()
        consumed_new_ids = self.consumed_new_display_ids.finish()
        return HunkReplacementTranslation(
            claimed_source_lines=self.claimed_source_lines.finish(),
            presence_baseline_references=self.presence_baseline_references,
            absence_claims=self.absence_claims,
            replacement_units=self.replacement_units,
            consumed_display_ids=LineRanges.from_ranges(
                range_pair
                for consumed_ids in (consumed_old_ids, consumed_new_ids)
                for range_pair in consumed_ids.ranges()
            ),
        )
