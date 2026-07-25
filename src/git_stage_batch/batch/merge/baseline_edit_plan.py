"""Storage-backed execution plans for baseline-coordinate edits."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from ...core.line_selection import LineRanges
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
        self._edits = workspace.record_vector(
            edit_capacity,
            "QQQQQ",
        )
        self._source_ranges = workspace.record_vector(
            source_range_capacity,
            "QQ",
        )
        self._next_ordinal = 0
        self._edits_are_ordered = True
        self._previous_sort_key: tuple[int, int] | None = None

    def __bool__(self) -> bool:
        return bool(self._edits)

    def add_removal(self, start: int, end: int) -> None:
        """Append one target removal without replacement source lines."""
        self._append_edit(start, end, len(self._source_ranges))

    def add_source_selection(
        self,
        start: int,
        end: int,
        source_selection: LineRanges,
    ) -> None:
        """Append one edit whose payload is a range-backed source selection."""
        source_range_start = len(self._source_ranges)
        for source_start, source_end in source_selection.ranges():
            self._source_ranges.append((source_start, source_end))
        self._append_edit(start, end, source_range_start)

    def add_positioned_source_lines(
        self,
        position: int,
        positioned_lines: Sequence[tuple[int, ...]],
        start_index: int,
        stop_index: int,
    ) -> None:
        """Append one insertion from sorted position/source-line records."""
        source_range_start = len(self._source_ranges)
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
            self._source_ranges.append((pending_start, pending_end))
            pending_start = source_line
            pending_end = source_line

        if pending_start is not None and pending_end is not None:
            self._source_ranges.append((pending_start, pending_end))
        self._append_edit(position, position, source_range_start)

    def removes_target_line(self, target_index: int) -> bool:
        """Return whether a planned non-insertion edit covers a target line."""
        return any(
            start <= target_index < end
            for (
                start,
                end,
                _ordinal,
                _source_range_start,
                _source_range_stop,
            ) in self._edits
        )

    def sort_and_validate(self) -> bool:
        """Sort edits by target position and reject overlapping target spans."""
        if not self._edits_are_ordered:
            sort_mapped_records(self._edits)
        previous_end = 0
        for (
            start,
            end,
            _ordinal,
            _source_range_start,
            _source_range_stop,
        ) in self._edits:
            if start < previous_end:
                return False
            previous_end = max(previous_end, end)
        return True

    def stream_lines(
        self,
        source_lines: Sequence[bytes],
        working_lines: Sequence[bytes],
    ) -> Iterator[bytes]:
        """Yield working lines with the validated edit plan applied."""
        position = 0
        for (
            start,
            end,
            _ordinal,
            source_range_start,
            source_range_stop,
        ) in self._edits:
            for working_index in range(position, start):
                yield working_lines[working_index]
            for source_range_index in range(
                source_range_start,
                source_range_stop,
            ):
                source_start, source_end = self._source_ranges[source_range_index]
                for source_line in range(source_start, source_end + 1):
                    yield source_lines[source_line - 1]
            position = end

        for working_index in range(position, len(working_lines)):
            yield working_lines[working_index]

    def _append_edit(
        self,
        start: int,
        end: int,
        source_range_start: int,
    ) -> None:
        source_range_stop = len(self._source_ranges)
        sort_key = (start, end)
        if self._previous_sort_key is not None and sort_key < self._previous_sort_key:
            self._edits_are_ordered = False
        self._edits.append(
            (
                start,
                end,
                self._next_ordinal,
                source_range_start,
                source_range_stop,
            )
        )
        self._previous_sort_key = sort_key
        self._next_ordinal += 1


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
        except Exception:
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
