"""Shared comparison logic for deriving semantic change runs from line alignment.

This module provides the common comparison pattern used by both attribution
and sift: compare two line spaces using match_lines, derive matched/unmatched
runs, pair them structurally, and emit semantic change units.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from ..batch.match import match_lines
from ..core.models import LineLevelChange


class SemanticChangeKind(Enum):
    """Type of semantic change between source and target."""

    PRESENCE = auto()
    """Pure addition in target (no coupled deletion from source)."""

    DELETION = auto()
    """Pure deletion from source (no coupled addition in target)."""

    REPLACEMENT = auto()
    """Deletion from source coupled with addition in target."""


@dataclass
class SemanticChangeRun:
    """A semantic change unit derived from source ↔ target comparison.

    Represents one of three patterns:
    - PRESENCE: target lines that have no corresponding source lines
    - DELETION: source lines that have no corresponding target lines
    - REPLACEMENT: paired source deletion and target addition runs

    All line numbers are 1-indexed.
    """

    kind: SemanticChangeKind

    # For PRESENCE and REPLACEMENT: target line numbers
    target_run: list[int] | None = None

    # For DELETION and REPLACEMENT: source line numbers
    source_run: list[int] | None = None

    # Structural anchor in target space (None = start of file)
    # For DELETION: where the deletion conceptually happens in target space
    # For REPLACEMENT: shared anchor for both sides
    target_anchor: int | None = None


def group_consecutive(line_numbers: list[int]) -> list[list[int]]:
    """Group line numbers into consecutive runs.

    Args:
        line_numbers: List of line numbers (may be unsorted)

    Returns:
        List of runs, where each run is a list of consecutive line numbers

    Example:
        [1, 2, 3, 5, 7, 8] → [[1, 2, 3], [5], [7, 8]]
    """
    if not line_numbers:
        return []

    sorted_lines = sorted(line_numbers)
    runs = [[sorted_lines[0]]]
    for line in sorted_lines[1:]:
        if line == runs[-1][-1] + 1:
            runs[-1].append(line)
        else:
            runs.append([line])
    return runs


def find_structural_predecessor(
    run: list[int],
    line_mapping: dict[int, int]
) -> int | None:
    """Find the last matched line before a run in the same coordinate space.

    Scans backwards from the start of the run to find the nearest line
    that has a mapping. This defines the structural anchor for the run.

    Args:
        run: List of consecutive line numbers
        line_mapping: Map from line numbers to their matches

    Returns:
        The last mapped line before the run, or None if at start-of-file

    Example:
        run = [5, 6, 7]
        mapping = {1: 1, 2: 2, 4: 4, 10: 10}
        → returns 4 (last mapped line before 5)
    """
    if not run:
        return None

    for candidate in range(run[0] - 1, 0, -1):
        if candidate in line_mapping:
            return candidate
    return None


def derive_semantic_change_runs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes]
) -> list[SemanticChangeRun]:
    """Derive semantic change runs from source ↔ target comparison.

    Uses match_lines for structural alignment, then groups unmatched lines
    into runs and pairs them using structural predecessors.

    Algorithm:
    1. Align source and target using match_lines
    2. Build bidirectional mappings
    3. Find unmatched lines in both spaces
    4. Group unmatched lines into consecutive runs
    5. Find structural predecessor for each run
    6. Pair source and target runs with matching anchors → REPLACEMENT
    7. Emit unpaired source runs → DELETION
    8. Emit unpaired target runs → PRESENCE

    Args:
        source_lines: Source file lines (bytes with newlines)
        target_lines: Target file lines (bytes with newlines)

    Returns:
        List of semantic change runs describing the delta
    """
    # Align source and target
    alignment = match_lines(source_lines=source_lines, target_lines=target_lines)
    reverse_alignment = match_lines(source_lines=target_lines, target_lines=source_lines)

    # Build bidirectional mappings
    source_to_target: dict[int, int] = {}
    target_to_source: dict[int, int] = {}

    for source_idx in range(len(source_lines)):
        source_line_num = source_idx + 1
        target_line_num = alignment.get_target_line_from_source_line(source_line_num)
        if target_line_num is not None:
            reverse_source_line = reverse_alignment.get_target_line_from_source_line(
                target_line_num
            )
            if reverse_source_line != source_line_num:
                continue
            source_to_target[source_line_num] = target_line_num
            target_to_source[target_line_num] = source_line_num

    # Find unmatched lines
    source_unmatched = [
        line_num
        for line_num in range(1, len(source_lines) + 1)
        if line_num not in source_to_target
    ]
    target_unmatched = [
        line_num
        for line_num in range(1, len(target_lines) + 1)
        if line_num not in target_to_source
    ]

    # Group into consecutive runs
    source_runs = group_consecutive(source_unmatched)
    target_runs = group_consecutive(target_unmatched)

    # Group runs by their structural anchors for unambiguous pairing
    # This implements conservative replacement detection: only pair when
    # there's exactly one source run and one target run with the same anchor
    source_runs_by_anchor: dict[int | None, list[tuple[int, list[int]]]] = {}
    target_runs_by_anchor: dict[int | None, list[tuple[int, list[int]]]] = {}

    for source_run_idx, source_run in enumerate(source_runs):
        source_anchor_in_source = find_structural_predecessor(source_run, source_to_target)
        source_anchor_in_target = (
            None if source_anchor_in_source is None
            else source_to_target.get(source_anchor_in_source)
        )
        source_runs_by_anchor.setdefault(source_anchor_in_target, []).append(
            (source_run_idx, source_run)
        )

    for target_run_idx, target_run in enumerate(target_runs):
        target_anchor = find_structural_predecessor(target_run, target_to_source)
        target_runs_by_anchor.setdefault(target_anchor, []).append(
            (target_run_idx, target_run)
        )

    # Pair runs only when anchor group is unambiguous (1-to-1)
    paired_source_runs: set[int] = set()
    paired_target_runs: set[int] = set()
    replacements: list[SemanticChangeRun] = []

    for anchor in set(source_runs_by_anchor.keys()) & set(target_runs_by_anchor.keys()):
        source_candidates = source_runs_by_anchor[anchor]
        target_candidates = target_runs_by_anchor[anchor]

        # Only pair if there's exactly one source run and one target run with this anchor
        # This is conservative: ambiguous cases become separate deletion + presence
        if len(source_candidates) == 1 and len(target_candidates) == 1:
            source_run_idx, source_run = source_candidates[0]
            target_run_idx, target_run = target_candidates[0]

            paired_source_runs.add(source_run_idx)
            paired_target_runs.add(target_run_idx)

            replacements.append(SemanticChangeRun(
                kind=SemanticChangeKind.REPLACEMENT,
                source_run=source_run,
                target_run=target_run,
                target_anchor=anchor
            ))

    # Emit remaining source runs as pure deletions
    deletions: list[SemanticChangeRun] = []
    for source_run_idx, source_run in enumerate(source_runs):
        if source_run_idx not in paired_source_runs:
            source_anchor_in_source = find_structural_predecessor(source_run, source_to_target)
            source_anchor_in_target = (
                None if source_anchor_in_source is None
                else source_to_target.get(source_anchor_in_source)
            )

            deletions.append(SemanticChangeRun(
                kind=SemanticChangeKind.DELETION,
                source_run=source_run,
                target_anchor=source_anchor_in_target
            ))

    # Emit remaining target runs as pure additions (presence)
    presences: list[SemanticChangeRun] = []
    for target_run_idx, target_run in enumerate(target_runs):
        if target_run_idx not in paired_target_runs:
            presences.append(SemanticChangeRun(
                kind=SemanticChangeKind.PRESENCE,
                target_run=target_run
            ))

    # Return in logical order: replacements, then deletions, then presences
    return replacements + deletions + presences


def derive_display_id_run_sets(
    line_changes: LineLevelChange,
    *,
    source_content: bytes,
    target_content: bytes,
) -> list[set[int]]:
    """Map semantic change runs onto display IDs in one rendered selection."""
    return derive_display_id_run_sets_from_lines(
        line_changes,
        source_lines=source_content.splitlines(keepends=True),
        target_lines=target_content.splitlines(keepends=True),
    )


def derive_display_id_run_sets_from_lines(
    line_changes: LineLevelChange,
    *,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> list[set[int]]:
    """Map semantic change runs from byte-line sequences onto display IDs."""
    semantic_runs = derive_semantic_change_runs(
        source_lines,
        target_lines,
    )
    return _display_id_run_sets_from_semantic_runs(line_changes, semantic_runs)


def derive_replacement_display_id_run_sets_from_lines(
    line_changes: LineLevelChange,
    *,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> list[set[int]]:
    """Map replacement runs from byte-line sequences onto display IDs."""
    semantic_runs = [
        run
        for run in derive_semantic_change_runs(source_lines, target_lines)
        if run.kind == SemanticChangeKind.REPLACEMENT
    ]
    return _display_id_run_sets_from_semantic_runs(line_changes, semantic_runs)


def _display_id_run_sets_from_semantic_runs(
    line_changes: LineLevelChange,
    semantic_runs: Sequence[SemanticChangeRun],
) -> list[set[int]]:
    run_sets: list[set[int]] = []
    for run in semantic_runs:
        display_ids = {
            line.id
            for line in line_changes.lines
            if line.id is not None and (
                (
                    run.kind == SemanticChangeKind.REPLACEMENT
                    and (
                        (line.kind == "-" and line.old_line_number in (run.source_run or []))
                        or (line.kind == "+" and line.new_line_number in (run.target_run or []))
                    )
                )
                or (
                    run.kind == SemanticChangeKind.DELETION
                    and line.kind == "-"
                    and line.old_line_number in (run.source_run or [])
                )
                or (
                    run.kind == SemanticChangeKind.PRESENCE
                    and line.kind == "+"
                    and line.new_line_number in (run.target_run or [])
                )
            )
        }
        if display_ids:
            run_sets.append(display_ids)

    return run_sets
