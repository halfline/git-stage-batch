"""Attach persistent baseline references to selected insertion lines."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.models import LineEntry, LineLevelChange
from ...core.text_lines import normalize_line_sequence_endings
from ..line_matching.match import match_lines
from ..merge.baseline_reference_positions import (
    baseline_reference_insertion_position,
)
from .references import BaselineReference


def _record_diff_baseline_references_for_additions(
    line_changes: LineLevelChange,
) -> None:
    """Attach insertion references from the old side of the displayed diff."""
    last_old_line: int | None = None
    last_old_text_bytes: bytes | None = None
    index = 0

    while index < len(line_changes.lines):
        line = line_changes.lines[index]
        if line.kind == "+":
            next_old_line: int | None = None
            next_old_text_bytes: bytes | None = None
            scan_index = index + 1
            while scan_index < len(line_changes.lines):
                next_line = line_changes.lines[scan_index]
                if (
                    next_line.kind in {" ", "-"}
                    and next_line.old_line_number is not None
                ):
                    next_old_line = next_line.old_line_number
                    next_old_text_bytes = next_line.text_bytes
                    break
                scan_index += 1

            while (
                index < len(line_changes.lines)
                and line_changes.lines[index].kind == "+"
            ):
                addition_line = line_changes.lines[index]
                addition_line.baseline_reference_after_line = last_old_line
                addition_line.baseline_reference_after_text_bytes = (
                    last_old_text_bytes
                )
                addition_line.has_baseline_reference_after = True
                addition_line.baseline_reference_before_line = next_old_line
                addition_line.baseline_reference_before_text_bytes = (
                    next_old_text_bytes
                )
                # A missing next old line is an explicit EOF boundary, not an
                # unknown second side.
                addition_line.has_baseline_reference_before = True
                index += 1
            continue

        if line.kind in {" ", "-"} and line.old_line_number is not None:
            last_old_line = line.old_line_number
            last_old_text_bytes = line.text_bytes
        index += 1


def _clear_addition_baseline_reference(addition_line: LineEntry) -> None:
    """Remove insertion metadata that does not fit the captured baseline."""
    addition_line.baseline_reference_after_line = None
    addition_line.baseline_reference_after_text_bytes = None
    addition_line.has_baseline_reference_after = False
    addition_line.baseline_reference_before_line = None
    addition_line.baseline_reference_before_text_bytes = None
    addition_line.has_baseline_reference_before = False


def _set_addition_baseline_reference(
    addition_line: LineEntry,
    baseline_lines: Sequence[bytes],
    insertion_position: int,
) -> None:
    """Record the two baseline lines surrounding an insertion position."""
    after_line = insertion_position or None
    before_line = (
        insertion_position + 1
        if insertion_position < len(baseline_lines)
        else None
    )
    addition_line.baseline_reference_after_line = after_line
    addition_line.baseline_reference_after_text_bytes = (
        bytes(baseline_lines[after_line - 1])
        if after_line is not None
        else None
    )
    addition_line.has_baseline_reference_after = True
    addition_line.baseline_reference_before_line = before_line
    addition_line.baseline_reference_before_text_bytes = (
        bytes(baseline_lines[before_line - 1])
        if before_line is not None
        else None
    )
    addition_line.has_baseline_reference_before = True


def _record_snapshot_baseline_references_for_additions(
    line_changes: LineLevelChange,
    *,
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> None:
    """Attach insertion references in captured baseline coordinates."""

    def reference_fits_baseline(line: LineEntry) -> bool:
        reference = BaselineReference(
            after_line=line.baseline_reference_after_line,
            after_content=line.baseline_reference_after_text_bytes,
            has_after_line=line.has_baseline_reference_after,
            before_line=line.baseline_reference_before_line,
            before_content=line.baseline_reference_before_text_bytes,
            has_before_line=line.has_baseline_reference_before,
        )
        position = baseline_reference_insertion_position(
            reference,
            baseline_lines,
        )
        if position is None:
            return False

        # Legacy line views may carry an after-only EOF reference. If the
        # baseline now continues past that position, rebuild it from snapshots.
        return line.has_baseline_reference_before or position == len(baseline_lines)

    with MappedRecordVector(
        len(line_changes.lines),
        "QQ",
    ) as addition_line_records:
        for line_index, line in enumerate(line_changes.lines):
            if (
                line.kind == "+"
                and line.source_line is not None
                and 1 <= line.source_line <= len(source_lines)
                and not reference_fits_baseline(line)
            ):
                addition_line_records.append((
                    line.source_line,
                    line_index,
                ))

        if not addition_line_records:
            return
        sort_mapped_records(addition_line_records)

        normalized_source_lines = normalize_line_sequence_endings(source_lines)
        normalized_baseline_lines = normalize_line_sequence_endings(baseline_lines)
        with match_lines(
            normalized_source_lines,
            normalized_baseline_lines,
        ) as mapping:
            mapped_pairs = mapping.mapped_line_pairs()
            previous_pair: tuple[int, int] | None = None
            next_pair = next(mapped_pairs, None)

            for source_line, line_index in addition_line_records:
                addition_line = line_changes.lines[line_index]

                while next_pair is not None and next_pair[0] < source_line:
                    previous_pair = next_pair
                    next_pair = next(mapped_pairs, None)

                if next_pair is not None and next_pair[0] == source_line:
                    target_line = next_pair[1]
                    after_line = target_line - 1 if target_line > 1 else None
                    previous_pair = next_pair
                    next_pair = next(mapped_pairs, None)
                else:
                    after_line = (
                        previous_pair[1] if previous_pair is not None else None
                    )
                    before_line = next_pair[1] if next_pair is not None else None
                    insertion_position = after_line or 0
                    expected_before_line = insertion_position + 1
                    actual_before_line = before_line or len(baseline_lines) + 1
                    if expected_before_line != actual_before_line:
                        # Target-only content leaves the relative insertion order
                        # ambiguous.
                        _clear_addition_baseline_reference(addition_line)
                        continue

                _set_addition_baseline_reference(
                    addition_line,
                    baseline_lines,
                    after_line or 0,
                )


def record_baseline_references_for_additions(
    line_changes: LineLevelChange,
    *,
    baseline_lines: Sequence[bytes] | None = None,
    source_lines: Sequence[bytes] | None = None,
) -> None:
    """Attach insertion references to addition lines for batch round trips."""
    _record_diff_baseline_references_for_additions(line_changes)
    if baseline_lines is None and source_lines is None:
        return
    if baseline_lines is None or source_lines is None:
        raise ValueError("baseline_lines and source_lines must be provided together")
    _record_snapshot_baseline_references_for_additions(
        line_changes,
        baseline_lines=baseline_lines,
        source_lines=source_lines,
    )
