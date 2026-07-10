"""Baseline restoration correspondence for batch discard."""

from __future__ import annotations

from array import array
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto

from .match import match_lines
from .line_range_view import LineRangeView


class RegionKind(Enum):
    """Region kind for baseline restoration correspondence."""

    EQUAL = auto()
    INSERT = auto()
    REPLACE_LINE_BY_LINE = auto()
    REPLACE_BY_HUNK = auto()


@dataclass
class BaselineRegion:
    """A source-space region with baseline restoration content."""

    source_start_line: int
    source_end_line: int
    baseline_lines: Sequence[bytes]
    kind: RegionKind
    region_id: int = 0


@dataclass
class BaselineCorrespondence:
    """Restoration correspondence from source lines back to baseline regions."""

    regions: list[BaselineRegion]
    _region_start_lines: array = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._region_start_lines = array(
            "Q",
            (region.source_start_line for region in self.regions),
        )

    def get_region_for_source_line(
        self,
        source_line: int,
    ) -> BaselineRegion | None:
        region_index = bisect_right(self._region_start_lines, source_line) - 1
        if region_index < 0:
            return None

        region = self.regions[region_index]
        if source_line > region.source_end_line:
            return None
        return region


@dataclass(slots=True)
class _BaselineCorrespondenceScanState:
    """Cursors and pending anchor-run bounds while building correspondence."""

    next_region_id: int = 1
    baseline_cursor: int = 0
    source_cursor: int = 0
    run_base_start: int | None = None
    run_source_start: int | None = None
    run_base_end: int = 0
    run_source_end: int = 0

    @property
    def has_run(self) -> bool:
        return self.run_base_start is not None and self.run_source_start is not None


def build_baseline_correspondence(
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> BaselineCorrespondence:
    """Build restoration correspondence from source lines to baseline regions."""
    regions: list[BaselineRegion] = []
    state = _BaselineCorrespondenceScanState()

    with match_lines(baseline_lines, source_lines) as mapping:
        for baseline_index in range(len(baseline_lines)):
            source_line = mapping.get_target_line_from_source_line(baseline_index + 1)
            if source_line is None:
                continue

            source_index = source_line - 1

            if not state.has_run:
                state = _start_baseline_anchor_run(
                    state,
                    baseline_index,
                    source_index,
                )
                continue

            if (
                baseline_index == state.run_base_end
                and source_index == state.run_source_end
            ):
                state = _extend_baseline_anchor_run(state)
                continue

            state = _flush_baseline_anchor_run(
                regions,
                state,
                baseline_lines,
                source_lines,
            )
            state = _start_baseline_anchor_run(
                state,
                baseline_index,
                source_index,
            )

    state = _flush_baseline_anchor_run(
        regions,
        state,
        baseline_lines,
        source_lines,
    )
    _append_baseline_gap_region(
        regions,
        state.next_region_id,
        baseline_lines,
        source_lines,
        state.baseline_cursor,
        len(baseline_lines),
        state.source_cursor,
        len(source_lines),
    )

    return BaselineCorrespondence(regions=regions)


def _start_baseline_anchor_run(
    state: _BaselineCorrespondenceScanState,
    baseline_index: int,
    source_index: int,
) -> _BaselineCorrespondenceScanState:
    """Return state with a new pending anchor run."""
    return _BaselineCorrespondenceScanState(
        next_region_id=state.next_region_id,
        baseline_cursor=state.baseline_cursor,
        source_cursor=state.source_cursor,
        run_base_start=baseline_index,
        run_source_start=source_index,
        run_base_end=baseline_index + 1,
        run_source_end=source_index + 1,
    )


def _extend_baseline_anchor_run(
    state: _BaselineCorrespondenceScanState,
) -> _BaselineCorrespondenceScanState:
    """Return state with the pending anchor run extended by one pair."""
    return _BaselineCorrespondenceScanState(
        next_region_id=state.next_region_id,
        baseline_cursor=state.baseline_cursor,
        source_cursor=state.source_cursor,
        run_base_start=state.run_base_start,
        run_source_start=state.run_source_start,
        run_base_end=state.run_base_end + 1,
        run_source_end=state.run_source_end + 1,
    )


def _flush_baseline_anchor_run(
    regions: list[BaselineRegion],
    state: _BaselineCorrespondenceScanState,
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> _BaselineCorrespondenceScanState:
    """Append gap/equal regions for a pending anchor run and advance cursors."""
    if not state.has_run:
        return state

    assert state.run_base_start is not None
    assert state.run_source_start is not None

    next_region_id = _append_baseline_gap_region(
        regions,
        state.next_region_id,
        baseline_lines,
        source_lines,
        state.baseline_cursor,
        state.run_base_start,
        state.source_cursor,
        state.run_source_start,
    )
    next_region_id = _append_baseline_region(
        regions,
        next_region_id,
        baseline_lines,
        state.run_base_start,
        state.run_base_end,
        state.run_source_start,
        state.run_source_end,
        RegionKind.EQUAL,
    )

    return _BaselineCorrespondenceScanState(
        next_region_id=next_region_id,
        baseline_cursor=state.run_base_end,
        source_cursor=state.run_source_end,
    )


def _append_baseline_gap_region(
    regions: list[BaselineRegion],
    next_region_id: int,
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
    base_start: int,
    base_end: int,
    src_start: int,
    src_end: int,
) -> int:
    """Append a source-space region for an unmatched baseline/source gap."""
    base_len = base_end - base_start
    src_len = src_end - src_start

    if base_len == 0 and src_len == 0:
        return next_region_id

    if src_len == 0:
        return next_region_id

    if base_len == 0:
        return _append_baseline_region(
            regions,
            next_region_id,
            baseline_lines,
            base_start,
            base_end,
            src_start,
            src_end,
            RegionKind.INSERT,
        )

    if base_len == src_len:
        baseline_segment = LineRangeView(baseline_lines, base_start, base_end)
        source_segment = LineRangeView(source_lines, src_start, src_end)

        with match_lines(baseline_segment, source_segment) as sub_mapping:
            all_baseline_mapped = all(
                sub_mapping.get_target_line_from_source_line(index + 1) is not None
                for index in range(len(baseline_segment))
            )
            all_source_mapped = all(
                sub_mapping.get_source_line_from_target_line(index + 1) is not None
                for index in range(len(source_segment))
            )

        kind = (
            RegionKind.REPLACE_LINE_BY_LINE
            if all_baseline_mapped and all_source_mapped
            else RegionKind.REPLACE_BY_HUNK
        )
        return _append_baseline_region(
            regions,
            next_region_id,
            baseline_lines,
            base_start,
            base_end,
            src_start,
            src_end,
            kind,
        )

    return _append_baseline_region(
        regions,
        next_region_id,
        baseline_lines,
        base_start,
        base_end,
        src_start,
        src_end,
        RegionKind.REPLACE_BY_HUNK,
    )


def _append_baseline_region(
    regions: list[BaselineRegion],
    next_region_id: int,
    baseline_lines: Sequence[bytes],
    base_start: int,
    base_end: int,
    src_start: int,
    src_end: int,
    kind: RegionKind,
) -> int:
    """Append one baseline correspondence region."""
    baseline_region_lines: Sequence[bytes]

    if src_start == src_end:
        return next_region_id

    if kind == RegionKind.INSERT:
        baseline_region_lines = ()
    else:
        baseline_region_lines = LineRangeView(baseline_lines, base_start, base_end)

    regions.append(
        BaselineRegion(
            source_start_line=src_start + 1,
            source_end_line=src_end,
            baseline_lines=baseline_region_lines,
            kind=kind,
            region_id=next_region_id,
        )
    )
    return next_region_id + 1
