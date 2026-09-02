"""Exact search helpers for line sequences."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .match_workspace import MatcherWorkspace
from .sequence_equality import line_slice_equals


@dataclass(frozen=True)
class TargetGap:
    """A concrete gap between target lines."""

    gap_index: int
    target_after_line: int | None
    target_before_line: int | None


def iter_exact_sequence_indexes(
    source_lines: Sequence[bytes],
    sequence: Sequence[bytes],
    *,
    workspace: MatcherWorkspace,
    start_index: int = 0,
    end_index: int | None = None,
) -> Iterator[int]:
    """Yield every exact sequence start in linear time."""
    sequence_count = len(sequence)
    if sequence_count == 0:
        return
    start_index = max(0, start_index)
    end_index = (
        len(source_lines)
        if end_index is None
        else min(
            len(source_lines),
            end_index,
        )
    )
    if start_index >= end_index or end_index - start_index < sequence_count:
        return

    prefix_lengths = workspace.int_vector(
        sequence_count,
        width=8,
        fill=0,
    )
    try:
        matched = 0
        for sequence_index in range(1, sequence_count):
            while matched and sequence[sequence_index] != sequence[matched]:
                matched = prefix_lengths[matched - 1]
            if sequence[sequence_index] == sequence[matched]:
                matched += 1
            prefix_lengths[sequence_index] = matched

        matched = 0
        for source_index in range(start_index, end_index):
            while matched and source_lines[source_index] != sequence[matched]:
                matched = prefix_lengths[matched - 1]
            if source_lines[source_index] == sequence[matched]:
                matched += 1
            if matched != sequence_count:
                continue
            yield source_index - sequence_count + 1
            matched = prefix_lengths[matched - 1]
    finally:
        workspace.close_resource(prefix_lengths)


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
        if left_count and not line_slice_equals(
            target_lines,
            gap_index - left_count,
            left_context,
        ):
            continue
        if right_count and not line_slice_equals(
            target_lines, gap_index, right_context
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
