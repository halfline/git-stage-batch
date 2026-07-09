"""Exact byte-line equality helpers for batch operations."""

from __future__ import annotations

from collections.abc import Sequence


def line_sequences_equal(
    left: Sequence[bytes],
    right: Sequence[bytes],
) -> bool:
    """Return whether two line sequences contain the same bytes."""
    return len(left) == len(right) and all(
        left[index] == right[index]
        for index in range(len(left))
    )


def line_slice_equals(
    lines: Sequence[bytes],
    start: int,
    expected: Sequence[bytes],
) -> bool:
    """Return whether a sequence slice equals the expected byte lines."""
    if start < 0 or start + len(expected) > len(lines):
        return False
    return all(
        lines[start + offset] == expected[offset]
        for offset in range(len(expected))
    )
