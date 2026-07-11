"""Scoped ownership acquisition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from ...core.buffer import LineBuffer


_OwnershipT = TypeVar("_OwnershipT")


@dataclass
class AcquiredBatchOwnership(Generic[_OwnershipT]):
    """Own buffers used by a scoped ownership value."""

    ownership: _OwnershipT
    buffers: list[LineBuffer]

    def close(self) -> None:
        """Close buffers held by the scoped ownership value."""
        for buffer in self.buffers:
            buffer.close()

    def __enter__(self) -> _OwnershipT:
        return self.ownership

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
