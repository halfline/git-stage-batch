"""Shared pytest helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import overload

import pytest


class _LineSequence(Sequence[bytes]):
    """Minimal non-list byte-line sequence for API contract tests."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = tuple(lines)

    def __len__(self) -> int:
        return len(self._lines)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[bytes, ...]: ...

    def __getitem__(self, index: int | slice) -> bytes | tuple[bytes, ...]:
        return self._lines[index]


@pytest.fixture
def line_sequence():
    """Return a minimal byte-line sequence type."""
    return _LineSequence
