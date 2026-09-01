"""Remember where selected additions belong in the original file."""

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
                # A missing next old line is an explicit EOF boundary, not an
                # unknown second side.
                line_changes.lines[index] = addition_line.with_baseline_reference(
                    after_line=last_old_line,
                    after_content=last_old_text_bytes,
                    has_after=True,
                    before_line=next_old_line,
                    before_content=next_old_text_bytes,
                    has_before=True,
                )
                index += 1
            continue

        if line.kind in {" ", "-"} and line.old_line_number is not None:
            last_old_line = line.old_line_number
            last_old_text_bytes = line.text_bytes
        index += 1


def _clear_addition_baseline_reference(addition_line: LineEntry) -> LineEntry:
    """Remove insertion metadata that does not fit the captured baseline."""
    return addition_line.with_baseline_reference(
        after_line=None,
        after_content=None,
        has_after=False,
        before_line=None,
        before_content=None,
        has_before=False,
    )


def _set_addition_baseline_reference(
    addition_line: LineEntry,
    baseline_lines: Sequence[bytes],
    insertion_position: int,
) -> LineEntry:
    """Record the two baseline lines surrounding an insertion position."""
    after_line = insertion_position or None
    before_line = (
        insertion_position + 1 if insertion_position < len(baseline_lines) else None
    )
    return addition_line.with_baseline_reference(
        after_line=after_line,
        after_content=(
            bytes(baseline_lines[after_line - 1]) if after_line is not None else None
        ),
        has_after=True,
        before_line=before_line,
        before_content=(
            bytes(baseline_lines[before_line - 1]) if before_line is not None else None
        ),
        has_before=True,
    )


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
                addition_line_records.append(
                    (
                        line.source_line,
                        line_index,
                    )
                )

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
                    after_line = previous_pair[1] if previous_pair is not None else None
                    before_line = next_pair[1] if next_pair is not None else None
                    insertion_position = after_line or 0
                    expected_before_line = insertion_position + 1
                    actual_before_line = before_line or len(baseline_lines) + 1
                    if expected_before_line != actual_before_line:
                        # Target-only content leaves the relative insertion order
                        # ambiguous.
                        line_changes.lines[line_index] = (
                            _clear_addition_baseline_reference(addition_line)
                        )
                        continue

                line_changes.lines[line_index] = _set_addition_baseline_reference(
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


def _selected_line_index(
    selected_records: Sequence[tuple[int, ...]],
    line_id: int,
) -> int | None:
    """Find a selected line in the sorted ID records."""
    low = 0
    high = len(selected_records)
    while low < high:
        middle = (low + high) // 2
        if selected_records[middle][0] < line_id:
            low = middle + 1
        else:
            high = middle
    if low >= len(selected_records) or selected_records[low][0] != line_id:
        return None
    return selected_records[low][1]


def _relocated_variant_opener_index(
    hunk_lines: Sequence[LineEntry],
    run_start: int,
    run_end: int,
    boundary: bytes,
) -> int | None:
    """Find changed new text paired with an older boundary in this run."""
    if len(boundary) < 3 or not any(
        hunk_lines[index].kind == "-"
        and len(hunk_lines[index].text_bytes) > len(boundary)
        and hunk_lines[index].text_bytes.startswith(boundary)
        for index in range(run_start, run_end)
    ):
        return None
    return next(
        (
            index
            for index in range(run_start, run_end)
            if hunk_lines[index].kind == "+"
            and len(hunk_lines[index].text_bytes) > len(boundary)
            and hunk_lines[index].text_bytes.startswith(boundary)
        ),
        None,
    )


def reanchor_selected_additions_after_relocated_context_boundary(
    hunk_lines: Sequence[LineEntry],
    selected_lines: Sequence[LineEntry],
    source_lines: Sequence[bytes],
) -> list[LineEntry]:
    """Keep additions to new text outside the old text's context.

    Git can display a boundary shared by the old and new text as unchanged.
    It then places all earlier additions before that boundary. Additions that
    belong to the new text must instead follow the complete old block.
    """
    refreshed_selection = list(selected_lines)
    with MappedRecordVector(len(selected_lines), "QQ") as selected_records:
        for selected_index, line in enumerate(selected_lines):
            if line.id is not None:
                selected_records.append((line.id, selected_index))
        if not selected_records:
            return refreshed_selection
        sort_mapped_records(selected_records)

        _reanchor_selected_additions_after_relocated_context_boundary(
            hunk_lines,
            refreshed_selection,
            selected_records,
            source_lines,
        )
    return refreshed_selection


def _reanchor_selected_additions_after_relocated_context_boundary(
    hunk_lines: Sequence[LineEntry],
    refreshed_selection: list[LineEntry],
    selected_records: Sequence[tuple[int, ...]],
    source_lines: Sequence[bytes],
) -> None:
    """Correct selected lines placed against the wrong changed boundary."""
    if not selected_records:
        return
    index = 0
    while index < len(hunk_lines):
        if hunk_lines[index].kind not in {"+", "-"}:
            index += 1
            continue
        run_start = index
        while index < len(hunk_lines) and hunk_lines[index].kind in {"+", "-"}:
            index += 1
        run_end = index
        if index >= len(hunk_lines):
            continue
        closing = hunk_lines[index]
        if closing.kind != " " or closing.old_line_number is None:
            continue
        opener_index = _relocated_variant_opener_index(
            hunk_lines,
            run_start,
            run_end,
            closing.text_bytes,
        )
        if opener_index is None:
            continue

        tail_end = index + 1
        while tail_end < len(hunk_lines):
            tail_line = hunk_lines[tail_end]
            if (
                tail_line.kind != " "
                or tail_line.old_line_number is None
                or tail_line.text_bytes
            ):
                break
            tail_end += 1
        after = hunk_lines[tail_end - 1]
        before = next(
            (
                hunk_lines[before_index]
                for before_index in range(tail_end, len(hunk_lines))
                if hunk_lines[before_index].old_line_number is not None
            ),
            None,
        )
        last_relocated_index: int | None = None
        for candidate_index in range(run_start, opener_index):
            candidate = hunk_lines[candidate_index]
            selected_index = (
                _selected_line_index(selected_records, candidate.id)
                if candidate.id is not None
                else None
            )
            if (
                candidate.kind != "+"
                or selected_index is None
                or not candidate.has_baseline_reference_before
                or candidate.baseline_reference_before_line != closing.old_line_number
            ):
                continue
            refreshed_selection[selected_index] = refreshed_selection[
                selected_index
            ].with_baseline_reference(
                after_line=after.old_line_number,
                after_content=after.text_bytes,
                has_after=True,
                before_line=(before.old_line_number if before is not None else None),
                before_content=(before.text_bytes if before is not None else None),
                has_before=True,
            )
            last_relocated_index = candidate_index

        if last_relocated_index is not None:
            separator_index = last_relocated_index + 1
            if (
                separator_index + 1 < opener_index
                and hunk_lines[separator_index].kind == "+"
                and not hunk_lines[separator_index].text_bytes
                and hunk_lines[separator_index + 1].kind == "+"
                and not hunk_lines[separator_index + 1].text_bytes
            ):
                separator = hunk_lines[separator_index]
                preceding_id = hunk_lines[last_relocated_index].id
                assert preceding_id is not None
                preceding_selected_index = _selected_line_index(
                    selected_records,
                    preceding_id,
                )
                assert preceding_selected_index is not None
                preceding = refreshed_selection[preceding_selected_index]
                separator_source_line = (
                    preceding.source_line + 1
                    if preceding.source_line is not None
                    else None
                )
                if (
                    separator_source_line is not None
                    and separator_source_line <= len(source_lines)
                    and source_lines[separator_source_line - 1]
                    == separator.text_bytes
                    + (b"\n" if separator.has_trailing_newline else b"")
                ):
                    refreshed_selection.append(
                        separator.with_source_line(
                            separator_source_line
                        ).with_baseline_reference(
                            after_line=after.old_line_number,
                            after_content=after.text_bytes,
                            has_after=True,
                            before_line=(
                                before.old_line_number if before is not None else None
                            ),
                            before_content=(
                                before.text_bytes if before is not None else None
                            ),
                            has_before=True,
                        )
                    )
