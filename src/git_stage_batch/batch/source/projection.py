"""Immutable source-coordinate projections for rendered diff selections."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Callable, Protocol

from ...core.coordinates import (
    BatchSourceSpace,
    DisplayLineId,
    FileSnapshot,
    SnapshotIdentity,
    require_snapshot_role,
)
from ...core.mapped_storage import ChunkedMappedRecordVector


_PROJECTION_CHUNK_CAPACITY = 8192


class RenderedSourceRow(Protocol):
    """Minimal compatibility view of a rendered row during migration."""

    @property
    def id(self) -> int | None: ...

    @property
    def source_line(self) -> int | None: ...

    @property
    def new_line_number(self) -> int | None: ...


AnonymousRowResolver = Callable[[int | None], int | None]


@dataclass(slots=True)
class SourceCoordinateProjection:
    """Storage-backed display-ID projection into one exact batch source.

    Only explicitly projected rows occupy records. Rendered-row annotations
    are not fallback authority because they may describe an older source.
    """

    view_identity: SnapshotIdentity
    source_snapshot: FileSnapshot[BatchSourceSpace]
    _records: ChunkedMappedRecordVector
    _anonymous_row_resolver: AnonymousRowResolver | None = None

    @classmethod
    def from_pairs(
        cls,
        *,
        view_identity: SnapshotIdentity,
        source_snapshot: FileSnapshot[BatchSourceSpace],
        pairs: Iterable[tuple[DisplayLineId, int | None]],
        capacity: int,
        anonymous_row_resolver: AnonymousRowResolver | None = None,
    ) -> SourceCoordinateProjection:
        require_snapshot_role(source_snapshot, BatchSourceSpace)
        if (
            not isinstance(view_identity, SnapshotIdentity)
            or view_identity.kind != "diff-view-sha256"
        ):
            raise ValueError("source projection requires an exact diff-view identity")
        if type(capacity) is not int or capacity < 0:
            raise ValueError("source projection capacity must be non-negative")
        if anonymous_row_resolver is not None and not callable(
            anonymous_row_resolver
        ):
            raise TypeError("anonymous source resolver must be callable")
        records = ChunkedMappedRecordVector(
            record_format="QQ",
            chunk_capacity=max(
                1,
                min(capacity, _PROJECTION_CHUNK_CAPACITY),
            ),
        )
        previous_id = 0
        try:
            for display_id, source_line in pairs:
                if len(records) >= capacity:
                    raise OverflowError("source projection capacity exceeded")
                if not isinstance(display_id, DisplayLineId):
                    raise TypeError("source projection requires DisplayLineId values")
                if display_id.value <= previous_id:
                    raise ValueError("source projection IDs must be strictly ordered")
                cls._validate_source_line(source_snapshot, source_line)
                records.append(
                    (
                        display_id.value,
                        0 if source_line is None else source_line,
                    )
                )
                previous_id = display_id.value
        except BaseException:
            records.close()
            raise
        return cls(
            view_identity,
            source_snapshot,
            records,
            anonymous_row_resolver,
        )

    @staticmethod
    def _validate_source_line(
        source_snapshot: FileSnapshot[BatchSourceSpace],
        source_line: int | None,
    ) -> None:
        if source_line is not None and (
            type(source_line) is not int
            or source_line <= 0
            or source_line > source_snapshot.line_count
        ):
            raise ValueError("source projection coordinate is outside snapshot")

    def source_line_for(self, line: RenderedSourceRow) -> int | None:
        """Return an exact projected coordinate, rejecting unprojected rows."""
        if line.id is None:
            if self._anonymous_row_resolver is None:
                raise ValueError("rendered row is absent from exact source projection")
            source_line = self._anonymous_row_resolver(line.new_line_number)
            self._validate_source_line(self.source_snapshot, source_line)
            return source_line
        start = 0
        end = len(self._records)
        while start < end:
            middle = (start + end) // 2
            display_id, _source_line = self._records[middle]
            if display_id < line.id:
                start = middle + 1
            else:
                end = middle
        if start < len(self._records):
            display_id, source_line = self._records[start]
            if display_id == line.id:
                return source_line or None
        raise ValueError("rendered row is absent from exact source projection")

    def require_view(self, identity: SnapshotIdentity) -> None:
        """Reject use with rendered rows from another exact diff view."""
        if self.view_identity != identity:
            raise ValueError("source projection belongs to another diff view")

    def close(self) -> None:
        """Release mapped projection storage."""
        self._records.close()

    def __enter__(self) -> SourceCoordinateProjection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
