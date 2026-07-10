"""Exact search helpers for line sequences."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .line_sequence_equality import line_slice_equals


@dataclass(frozen=True)
class TargetGap:
    """A concrete gap between target lines."""

    gap_index: int
    target_after_line: int | None
    target_before_line: int | None


def iter_exact_sequence_occurrences(
    lines: Sequence[bytes],
    sequence: Sequence[bytes],
    *,
    start: int = 0,
    end: int | None = None,
    max_results: int | None = None,
) -> Iterator[int]:
    """Yield exact sequence start offsets in ascending order."""
    if end is None:
        end = len(lines)
    start = max(start, 0)
    end = min(end, len(lines))
    if not sequence:
        return
    if start > end:
        return

    result_count = 0
    sequence_length = len(sequence)
    last_start = end - sequence_length
    for index in range(start, last_start + 1):
        if all(
            lines[index + offset] == sequence[offset]
            for offset in range(sequence_length)
        ):
            yield index
            result_count += 1
            if max_results is not None and result_count >= max_results:
                return


def iter_exact_context_gaps(
    target_lines: Sequence[bytes],
    *,
    left_context: Sequence[bytes],
    right_context: Sequence[bytes],
    start_gap: int,
    end_gap: int,
    max_results: int | None = None,
) -> Iterator[TargetGap]:
    """Yield target gaps whose surrounding context matches exactly."""
    start_gap = max(start_gap, 0)
    end_gap = min(end_gap, len(target_lines))
    if start_gap > end_gap:
        return

    result_count = 0
    left_count = len(left_context)
    right_count = len(right_context)
    for gap_index in range(start_gap, end_gap + 1):
        if gap_index < left_count:
            continue
        if gap_index + right_count > len(target_lines):
            continue
        if (
            left_count
            and not line_slice_equals(
                target_lines,
                gap_index - left_count,
                left_context,
            )
        ):
            continue
        if (
            right_count
            and not line_slice_equals(target_lines, gap_index, right_context)
        ):
            continue
        yield TargetGap(
            gap_index=gap_index,
            target_after_line=None if gap_index == 0 else gap_index,
            target_before_line=(
                None if gap_index == len(target_lines) else gap_index + 1
            ),
        )
        result_count += 1
        if max_results is not None and result_count >= max_results:
            return
