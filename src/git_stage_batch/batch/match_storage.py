"""Mapped storage helpers for structural matching."""

from __future__ import annotations

from pathlib import Path

from ..utils.mapped_storage import (
    ManagedMappedResources,
    MappedIntVector,
    MappedRecordVector,
)


class MatcherWorkspace(ManagedMappedResources):
    """Own temporary mapped matcher scratch resources."""

    def __init__(self, *, spool_dir: str | Path | None = None) -> None:
        super().__init__()
        self._spool_dir = spool_dir

    def int_vector(
        self,
        length: int,
        *,
        width: int = 8,
        fill: int = 0,
    ) -> MappedIntVector:
        """Create and track a fixed-width integer vector."""
        vector = MappedIntVector(
            length,
            width=width,
            fill=fill,
            spool_dir=self._spool_dir,
        )
        return self.track(vector)  # type: ignore[return-value]

    def record_vector(
        self,
        capacity: int,
        record_format: str,
        *,
        length: int | None = None,
    ) -> MappedRecordVector:
        """Create and track a fixed-record vector."""
        vector = MappedRecordVector(
            capacity,
            record_format,
            length=length,
            spool_dir=self._spool_dir,
        )
        return self.track(vector)  # type: ignore[return-value]
