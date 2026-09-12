"""Forward projection of replacement spans through their origin runs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Generic, TypeVar
from ...core.coordinates import LineBoundary, LineSpan, SnapshotSpan
from .line_entries import replacement_unit_origin_for_line_run
from .replacement_line_runs import ReplacementLineRun
from .replacement_units import ReplacementUnitOrigin
from .replacement_origins import (
    ReplacementOrigin,
    ReplacementOriginSourceProjection,
    SameStreamReplacementOrigin,
    ProjectedReplacementOrigin,
)

OriginSourceSpace = TypeVar("OriginSourceSpace")


class ReplacementOriginCursor(Generic[OriginSourceSpace]):
    """Borrow an origin iterator and retain at most one projected run."""

    def __init__(
        self,
        replacement_origin: ReplacementOrigin,
        origin_run_iterator: Iterator[ReplacementLineRun],
        replacement_origin_source_projection: ReplacementOriginSourceProjection[
            OriginSourceSpace
        ]
        | None,
    ) -> None:
        self.replacement_origin = replacement_origin
        self.origin_run_iterator = origin_run_iterator
        self.replacement_origin_source_projection = replacement_origin_source_projection
        self.replacement_origin_source_lines = (
            replacement_origin.source_lines
            if isinstance(
                replacement_origin,
                (SameStreamReplacementOrigin, ProjectedReplacementOrigin),
            )
            else None
        )
        self.next_origin_run = next(origin_run_iterator, None)
        self.cached_origin_run: ReplacementLineRun | None = None
        self.cached_origin: ReplacementUnitOrigin | None = None

    def project(
        self,
        new_start: int,
        new_end: int,
        *,
        replacement_run: ReplacementLineRun,
        align_suffix: bool = False,
    ) -> tuple[ReplacementUnitOrigin, int, int] | None:
        """Project a displayed replacement range through live HEAD."""

        if self.replacement_origin_source_lines is None:
            return None

        origin_run: ReplacementLineRun | None
        if isinstance(self.replacement_origin, SameStreamReplacementOrigin):
            origin_run = replacement_run
        elif isinstance(self.replacement_origin, ProjectedReplacementOrigin):
            while (
                self.next_origin_run is not None
                and self.next_origin_run.new_end < new_start
            ):
                self.next_origin_run = next(self.origin_run_iterator, None)
            origin_run = self.next_origin_run
        else:
            return None
        if (
            origin_run is None
            or origin_run.new_start > new_start
            or new_end > origin_run.new_end
        ):
            return None

        if new_start == origin_run.new_start and new_end == origin_run.new_end:
            origin_old_start = origin_run.old_start
            origin_old_end = origin_run.old_end
        else:
            origin_old_count = origin_run.old_end - origin_run.old_start + 1
            origin_new_count = origin_run.new_end - origin_run.new_start + 1
            selected_new_count = new_end - new_start + 1
            if origin_old_count == origin_new_count:
                origin_old_start = (
                    origin_run.old_start + new_start - origin_run.new_start
                )
                origin_old_end = origin_old_start + selected_new_count - 1
            elif (
                align_suffix
                and new_end == origin_run.new_end
                and selected_new_count <= origin_old_count
            ):
                origin_old_end = origin_run.old_end
                origin_old_start = origin_old_end - selected_new_count + 1
            else:
                return None

        if self.cached_origin_run != origin_run:
            self.cached_origin_run = origin_run
            self.cached_origin = replacement_unit_origin_for_line_run(
                origin_run,
                old_file_lines=self.replacement_origin_source_lines,
            )
            if self.replacement_origin_source_projection is not None:
                source_span = self.replacement_origin_source_projection.translate_span(
                    SnapshotSpan(
                        self.replacement_origin_source_projection.source_snapshot,
                        LineSpan(
                            LineBoundary(origin_run.new_start - 1),
                            LineBoundary(origin_run.new_end),
                        ),
                    )
                )
                self.cached_origin = (
                    self.cached_origin.with_batch_source_span(source_span)
                    if source_span is not None
                    else None
                )
        if self.cached_origin is None:
            return None
        return self.cached_origin, origin_old_start, origin_old_end
