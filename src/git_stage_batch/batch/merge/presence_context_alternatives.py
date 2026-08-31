"""Find a source section by comparing the lines around a selection."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import overload

from ...core.coordinates import (
    BatchSourceSpace,
    LineBoundary,
    LineSpan,
    WorktreeSpace,
)
from ...core.line_selection import LineRanges
from ..line_matching.line_mapping import LineMapping, allocate_line_mapping
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.sequence_search import iter_exact_sequence_indexes


@dataclass(frozen=True, slots=True)
class PresenceContextAlternativeRun:
    """One selected run and where it is missing from the target."""

    source: LineSpan[BatchSourceSpace]
    target_gap: LineBoundary[WorktreeSpace]

    def __post_init__(self) -> None:
        if len(self.source) == 0:
            raise ValueError("presence context alternative run must be non-empty")


@dataclass(frozen=True, slots=True)
class ResolvedPresenceContextAlternative:
    """A source section that matches after selected lines are removed.

    The source section and target position use different line-number types.
    The complete line map is built later.
    """

    source_context: LineSpan[BatchSourceSpace]
    target: LineSpan[WorktreeSpace]
    runs: tuple[PresenceContextAlternativeRun, ...]

    def __post_init__(self) -> None:
        if not self.runs:
            raise ValueError("presence context alternative has no claimed runs")
        if self.target.start.offset != 0:
            raise ValueError("presence context alternative target must start at zero")

        previous_source_end = self.source_context.start.offset
        previous_target_gap = self.target.start.offset
        selected_count = 0
        for run in self.runs:
            if (
                run.source.start.offset < previous_source_end
                or run.source.start.offset < self.source_context.start.offset
                or run.source.end.offset > self.source_context.end.offset
            ):
                raise ValueError(
                    "presence context alternative runs are outside source order"
                )
            if not (
                previous_target_gap <= run.target_gap.offset <= self.target.end.offset
            ):
                raise ValueError(
                    "presence context alternative gaps are outside target order"
                )
            previous_source_end = run.source.end.offset
            previous_target_gap = run.target_gap.offset
            selected_count += len(run.source)

        if len(self.source_context) != len(self.target) + selected_count:
            raise ValueError("presence context alternative extents do not balance")


class _SourceViewWithoutSelectedLines(Sequence[bytes]):
    """Read the unselected source lines without copying them."""

    def __init__(
        self,
        source_lines: Sequence[bytes],
        selected_lines: LineRanges,
        *,
        segment_starts: tuple[int, ...] | None = None,
        source_starts: tuple[int, ...] | None = None,
        indices: range | None = None,
    ) -> None:
        self._source_lines = source_lines
        if segment_starts is None or source_starts is None:
            segment_starts, source_starts, line_count = self._build_segments(
                len(source_lines),
                selected_lines,
            )
            self._indices = range(line_count)
        else:
            self._indices = range(0) if indices is None else indices
        self._segment_starts = segment_starts
        self._source_starts = source_starts

    @staticmethod
    def _build_segments(
        source_line_count: int,
        selected_lines: LineRanges,
    ) -> tuple[tuple[int, ...], tuple[int, ...], int]:
        segment_starts: list[int] = []
        source_starts: list[int] = []
        source_index = 0
        elided_index = 0
        for selected_start, selected_end in selected_lines.ranges():
            selected_start_index = selected_start - 1
            if selected_start_index > source_index:
                segment_starts.append(elided_index)
                source_starts.append(source_index)
                elided_index += selected_start_index - source_index
            source_index = selected_end
        if source_index < source_line_count:
            segment_starts.append(elided_index)
            source_starts.append(source_index)
            elided_index += source_line_count - source_index
        return tuple(segment_starts), tuple(source_starts), elided_index

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return _SourceViewWithoutSelectedLines(
                self._source_lines,
                LineRanges.empty(),
                segment_starts=self._segment_starts,
                source_starts=self._source_starts,
                indices=self._indices[index],
            )
        return self._source_lines[self.source_line_at(index) - 1]

    def source_line_at(self, index: int) -> int:
        """Return the one-based source line for one view index."""
        try:
            elided_index = self._indices[index]
        except IndexError as error:
            raise IndexError(index) from error
        segment_index = bisect_right(self._segment_starts, elided_index) - 1
        if segment_index < 0:
            raise IndexError(index)
        source_index = self._source_starts[segment_index] + (
            elided_index - self._segment_starts[segment_index]
        )
        return source_index + 1


def resolve_presence_context_alternative(
    presence_lines: LineRanges,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> ResolvedPresenceContextAlternative | None:
    """Find one matching source section after removing selected lines.

    Accept it only when it covers every selected range and no other section
    also matches.
    """
    ranges = presence_lines.ranges()
    if not ranges or not target_lines:
        return None
    if ranges[-1][1] > len(source_lines):
        # Range validation reports the public error for this case.
        return None

    run_gaps: list[int] = []
    selected_before = 0
    for source_start, source_end in ranges:
        run_gaps.append(source_start - 1 - selected_before)
        selected_before += source_end - source_start + 1

    first_run_gap = run_gaps[0]
    last_run_gap = run_gaps[-1]
    elided_source = _SourceViewWithoutSelectedLines(source_lines, presence_lines)
    candidate_index: int | None = None
    match_indexes = None
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        match_indexes = iter_exact_sequence_indexes(
            elided_source,
            target_lines,
            workspace=workspace,
        )
        try:
            for match_index in match_indexes:
                match_end = match_index + len(target_lines)
                if not (match_index <= first_run_gap and last_run_gap <= match_end):
                    continue
                if candidate_index is not None:
                    return None
                candidate_index = match_index
        finally:
            close_matches = getattr(match_indexes, "close", None)
            if close_matches is not None:
                close_matches()

    if candidate_index is None:
        return None

    first_claimed_line = ranges[0][0]
    last_claimed_line = ranges[-1][1]
    first_context_line = elided_source.source_line_at(candidate_index)
    last_context_line = elided_source.source_line_at(
        candidate_index + len(target_lines) - 1
    )
    source_context: LineSpan[BatchSourceSpace] = LineSpan(
        LineBoundary(min(first_claimed_line, first_context_line) - 1),
        LineBoundary(max(last_claimed_line, last_context_line)),
    )
    if source_context.start.offset == 0 and source_context.end.offset == len(
        source_lines
    ):
        # This covers the whole source after selected lines are removed. Use
        # the normal mapping path, which can reuse an existing mapping.
        return None
    target_span: LineSpan[WorktreeSpace] = LineSpan(
        LineBoundary(0),
        LineBoundary(len(target_lines)),
    )
    return ResolvedPresenceContextAlternative(
        source_context,
        target_span,
        tuple(
            PresenceContextAlternativeRun(
                LineSpan(
                    LineBoundary(source_start - 1),
                    LineBoundary(source_end),
                ),
                LineBoundary(run_gap - candidate_index),
            )
            for (source_start, source_end), run_gap in zip(
                ranges,
                run_gaps,
                strict=True,
            )
        ),
    )


def build_presence_context_alternative_mapping(
    alternative: ResolvedPresenceContextAlternative,
    *,
    source_line_count: int,
    target_line_count: int,
    spool_dir: str | Path | None = None,
) -> LineMapping:
    """Build a full line map for the matching source section."""
    if alternative.source_context.end.offset > source_line_count:
        raise ValueError("presence context alternative exceeds its source snapshot")
    if alternative.target.end.offset != target_line_count:
        raise ValueError("presence context alternative target extent changed")

    mapping = allocate_line_mapping(
        source_line_count,
        target_line_count,
        spool_dir=spool_dir,
    )
    try:
        target_index = 0
        run_index = 0
        runs = alternative.runs
        source_offset = alternative.source_context.start.offset
        while source_offset < alternative.source_context.end.offset:
            if (
                run_index < len(runs)
                and source_offset == runs[run_index].source.start.offset
            ):
                run = runs[run_index]
                if target_index != run.target_gap.offset:
                    raise ValueError("presence context alternative gap changed")
                source_offset = run.source.end.offset
                run_index += 1
                continue

            source_line = source_offset + 1
            target_line = target_index + 1
            mapping.source_to_target[source_offset] = target_line
            mapping.target_to_source[target_index] = source_line
            source_offset += 1
            target_index += 1

        if run_index != len(runs) or target_index != target_line_count:
            raise ValueError("presence context alternative mapping is incomplete")
        return mapping
    except BaseException:
        mapping.close()
        raise
