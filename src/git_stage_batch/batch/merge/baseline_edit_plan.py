"""Storage-backed execution plans for baseline-coordinate edits."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from ...core.mapped_storage import sort_mapped_records
from ..line_matching.match_workspace import MatcherWorkspace


class BaselineEditPlan:
    """Target edits with source payloads retained as mapped line ranges."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        *,
        edit_capacity: int,
        source_range_capacity: int,
    ) -> None:
        self._target_spans = workspace.record_vector(
            edit_capacity,
            "QQ",
        )
        self._payload_ranges = workspace.record_vector(
            source_range_capacity,
            "QQQ",
        )
        self._target_spans_are_ordered = True
        self._payload_ranges_are_ordered = True
        self._previous_target_span: tuple[int, int] | None = None
        self._previous_payload_range: tuple[int, int, int] | None = None

    def __bool__(self) -> bool:
        return bool(self._target_spans or self._payload_ranges)

    def add_removal(self, start: int, end: int) -> None:
        """Append one target removal without replacement source lines."""
        self._add_target_span(start, end)

    def add_source_ranges(
        self,
        start: int,
        end: int,
        source_ranges: Iterable[tuple[int, int]],
    ) -> None:
        """Append one edit whose payload comes from source ranges."""
        self._add_target_span(start, end)
        for source_start, source_end in source_ranges:
            self._add_payload_range(start, source_start, source_end)

    def add_positioned_source_lines(
        self,
        position: int,
        positioned_lines: Sequence[tuple[int, ...]],
        start_index: int,
        stop_index: int,
    ) -> None:
        """Append one insertion from sorted position/source-line records."""
        pending_start: int | None = None
        pending_end: int | None = None

        for record_index in range(start_index, stop_index):
            record_position, source_line = positioned_lines[record_index]
            if record_position != position:
                raise ValueError("insertion group contains multiple positions")
            if pending_start is None or pending_end is None:
                pending_start = source_line
                pending_end = source_line
                continue
            if source_line == pending_end + 1:
                pending_end = source_line
                continue
            self._add_payload_range(position, pending_start, pending_end)
            pending_start = source_line
            pending_end = source_line

        if pending_start is not None and pending_end is not None:
            self._add_payload_range(position, pending_start, pending_end)

    def removes_target_line(self, target_index: int) -> bool:
        """Return whether a planned non-insertion edit covers a target line."""
        return any(
            start <= target_index < end
            for start, end in self._target_spans
        )

    def sort_and_validate(self) -> bool:
        """Sort plan records and reject overlapping target or source spans."""
        if not self._target_spans_are_ordered:
            sort_mapped_records(self._target_spans)
            self._target_spans_are_ordered = True
        if not self._payload_ranges_are_ordered:
            sort_mapped_records(self._payload_ranges)
            self._payload_ranges_are_ordered = True
        return (
            self._target_spans_are_valid()
            and self._payload_ranges_are_valid()
            and self._payload_positions_avoid_target_interiors()
        )

    def stream_lines(
        self,
        source_lines: Sequence[bytes],
        working_lines: Sequence[bytes],
    ) -> Iterator[bytes]:
        """Yield working lines with the validated edit plan applied."""
        target_span_index = 0
        payload_range_index = 0
        position = 0
        while target_span_index < len(self._target_spans) or payload_range_index < len(
            self._payload_ranges
        ):
            target_position = (
                self._target_spans[target_span_index][0]
                if target_span_index < len(self._target_spans)
                else None
            )
            payload_position = (
                self._payload_ranges[payload_range_index][0]
                if payload_range_index < len(self._payload_ranges)
                else None
            )
            if target_position is None:
                next_position = payload_position
            elif payload_position is None:
                next_position = target_position
            else:
                next_position = min(target_position, payload_position)
            assert next_position is not None

            for working_index in range(position, next_position):
                yield working_lines[working_index]

            while (
                payload_range_index < len(self._payload_ranges)
                and self._payload_ranges[payload_range_index][0] == next_position
            ):
                _target_position, source_start, source_end = self._payload_ranges[
                    payload_range_index
                ]
                for source_line in range(source_start, source_end + 1):
                    yield source_lines[source_line - 1]
                payload_range_index += 1

            if (
                target_span_index < len(self._target_spans)
                and self._target_spans[target_span_index][0] == next_position
            ):
                _target_start, position = self._target_spans[target_span_index]
                target_span_index += 1
            else:
                position = next_position

        for working_index in range(position, len(working_lines)):
            yield working_lines[working_index]

    def _add_target_span(
        self,
        start: int,
        end: int,
    ) -> None:
        if start == end:
            return
        span = (start, end)
        if self._previous_target_span is not None and span < self._previous_target_span:
            self._target_spans_are_ordered = False
        self._target_spans.append(span)
        self._previous_target_span = span

    def _add_payload_range(
        self,
        target_position: int,
        source_start: int,
        source_end: int,
    ) -> None:
        payload_range = (target_position, source_start, source_end)
        if (
            self._previous_payload_range is not None
            and payload_range < self._previous_payload_range
        ):
            self._payload_ranges_are_ordered = False
        self._payload_ranges.append(payload_range)
        self._previous_payload_range = payload_range

    def _target_spans_are_valid(self) -> bool:
        previous_end = 0
        for start, end in self._target_spans:
            if end <= start or start < previous_end:
                return False
            previous_end = end
        return True

    def _payload_ranges_are_valid(self) -> bool:
        previous_target: int | None = None
        previous_source_end = 0
        for target_position, source_start, source_end in self._payload_ranges:
            if source_start < 1 or source_end < source_start:
                return False
            if target_position != previous_target:
                previous_target = target_position
            elif source_start <= previous_source_end:
                return False
            previous_source_end = source_end
        return True

    def _payload_positions_avoid_target_interiors(self) -> bool:
        target_span_index = 0
        for target_position, _source_start, _source_end in self._payload_ranges:
            while (
                target_span_index < len(self._target_spans)
                and self._target_spans[target_span_index][1] <= target_position
            ):
                target_span_index += 1
            if target_span_index >= len(self._target_spans):
                return True
            target_start, target_end = self._target_spans[target_span_index]
            if target_start < target_position < target_end:
                return False
        return True


class BaselineEditStream(Iterator[bytes]):
    """Own mapped edit-plan storage until output ends or closes."""

    def __init__(
        self,
        plan: BaselineEditPlan,
        source_lines: Sequence[bytes],
        working_lines: Sequence[bytes],
        workspace: MatcherWorkspace,
    ) -> None:
        self._lines = plan.stream_lines(source_lines, working_lines)
        self._workspace: MatcherWorkspace | None = workspace

    def __iter__(self) -> BaselineEditStream:
        return self

    def __next__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            self.close()
            raise
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release the output generator and its mapped planning storage."""
        workspace = self._workspace
        if workspace is None:
            return
        self._workspace = None
        try:
            self._lines.close()
        finally:
            workspace.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
