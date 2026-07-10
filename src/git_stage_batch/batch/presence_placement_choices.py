"""Placement choices for missing presence-claim runs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib

from ..core.line_selection import LineSelection
from .line_mapping import LineMapping
from .line_sequence_equality import line_slice_equals as _line_slice_matches
from .line_sequence_search import iter_exact_context_gaps
from .presence_missing_claims import (
    mapped_missing_source_lines as _mapped_missing_source_lines,
)


@dataclass(frozen=True)
class PresenceChoice:
    choice_index: int
    gap_index: int
    run_start: int
    run_end: int
    target_after_line: int | None
    target_before_line: int | None


def presence_choices_for_missing_claimed_run(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: LineSelection,
    mapping: LineMapping,
    *,
    max_results: int,
) -> tuple[str | None, tuple["PresenceChoice", ...]]:
    missing_claimed = _mapped_missing_source_lines(
        presence_line_set,
        len(source_lines),
        mapping,
    )
    ranges = list(missing_claimed.ranges())
    if len(ranges) != 1:
        return None, ()

    run_start, run_end = ranges[0]
    before_source_line = run_start - 1
    after_source_line = run_end + 1
    if before_source_line < 1 or after_source_line > len(source_lines):
        return None, ()
    before_target_line = mapping.get_target_line_from_source_line(before_source_line)
    after_target_line = mapping.get_target_line_from_source_line(after_source_line)
    if before_target_line is None or after_target_line is None:
        return None, ()
    if before_target_line >= after_target_line:
        return None, ()

    left_context = (bytes(source_lines[before_source_line - 1]),)
    right_context = (bytes(source_lines[after_source_line - 1]),)
    claimed_run = source_lines[run_start - 1:run_end]
    key = presence_ambiguity_key(
        run_start,
        run_end,
        claimed_run,
        before_source_line,
        after_source_line,
    )
    choices: list[PresenceChoice] = []
    for gap in iter_exact_context_gaps(
        working_lines,
        left_context=left_context,
        right_context=right_context,
        start_gap=before_target_line,
        end_gap=after_target_line - 1,
        max_results=max_results,
    ):
        if _line_slice_matches(working_lines, gap.gap_index, claimed_run):
            continue
        choices.append(
            PresenceChoice(
                choice_index=len(choices) + 1,
                gap_index=gap.gap_index,
                run_start=run_start,
                run_end=run_end,
                target_after_line=gap.target_after_line,
                target_before_line=gap.target_before_line,
            )
        )
    return key, tuple(choices)


def presence_ambiguity_key(
    run_start: int,
    run_end: int,
    claimed_run: Sequence[bytes],
    before_source_line: int,
    after_source_line: int,
) -> str:
    hasher = hashlib.sha256()
    for line in claimed_run:
        hasher.update(line)
    digest = hasher.hexdigest()[:12]
    return (
        f"presence:{run_start}-{run_end}:claimed:{digest}:"
        f"between:{before_source_line}-{after_source_line}"
    )


def presence_ambiguity_target_line_range(
    choices: Sequence[PresenceChoice],
    target_line_count: int,
) -> tuple[int, int] | None:
    """Return existing target lines spanning compatible insertion gaps."""
    if target_line_count == 0:
        return None

    positions = [choice.gap_index for choice in choices]
    start = max(1, min(positions))
    end = min(target_line_count, max(positions) + 1)
    if start > end:
        return None
    return start, end
