"""Selected-line source coordinate refresh helpers."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.models import LineEntry
from ..line_matching.lineage import BatchSourceLineage
from ..line_matching.match import match_lines
from ..ownership.translation import detect_stale_batch_source_for_selection


def _line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


def selected_lines_fit_source(
    selected_lines: list,
    source_lines: Sequence[bytes],
) -> bool:
    """Return whether selected presence lines can be claimed from source bytes."""
    if detect_stale_batch_source_for_selection(selected_lines):
        return False

    for line in selected_lines:
        if line.kind not in (" ", "+"):
            continue
        if line.source_line is None:
            return False

        source_index = line.source_line - 1
        if source_index < 0 or source_index >= len(source_lines):
            return False
        if source_lines[source_index] != _line_entry_content(line):
            return False

    return True


def refresh_selected_lines_against_new_source(selected_lines: list) -> list:
    """Re-annotate selected lines for a first-time batch source.

    This helper is only used before a batch source exists.
    The initial batch source commit will be created from the same working tree
    snapshot that the selected_lines were derived from, with no transformations
    applied. This means working tree line N in the snapshot maps to batch source
    line N in the new source commit.

    This invariant is maintained by create_batch_source_commit(), which creates
    the first source from the current working tree state. Advanced batch sources
    use refresh_selected_lines_against_source_lines() instead because they
    may preserve already-owned lines that are absent from the working tree.

    For first-time source creation, the mapping is trivial:
    - Context/addition line: working tree line N -> batch source line N
    - Deletion line: uses last known source line as anchor

    Args:
        selected_lines: LineEntry objects with potentially stale source_line values

    Returns:
        New list of LineEntry objects with refreshed source_line values
    """
    last_source_line = None
    reannotated_lines = []

    for line in selected_lines:
        if line.kind in (" ", "+"):
            source_line = line.new_line_number
            if source_line is not None:
                last_source_line = source_line
        elif line.kind == "-":
            source_line = last_source_line
            if (
                source_line is None
                and line.old_line_number is not None
                and line.old_line_number > 1
            ):
                source_line = line.old_line_number - 1
        else:
            source_line = None

        reannotated_lines.append(LineEntry(
            id=line.id,
            kind=line.kind,
            old_line_number=line.old_line_number,
            new_line_number=line.new_line_number,
            text_bytes=line.text_bytes,
            source_line=source_line,
            baseline_reference_after_line=line.baseline_reference_after_line,
            baseline_reference_after_text_bytes=line.baseline_reference_after_text_bytes,
            has_baseline_reference_after=line.has_baseline_reference_after,
            baseline_reference_before_line=line.baseline_reference_before_line,
            baseline_reference_before_text_bytes=line.baseline_reference_before_text_bytes,
            has_baseline_reference_before=line.has_baseline_reference_before,
        ))

    return reannotated_lines


def refresh_selected_lines_against_source_lines(
    selected_lines: list,
    *,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    lineage: BatchSourceLineage | None = None,
) -> list:
    """Re-annotate selected lines against source and working-tree line sequences."""
    mapping = None
    if lineage is None:
        mapping = match_lines(source_lines, working_lines)

    try:
        def map_working_line(line_number: int | None) -> int | None:
            if line_number is None:
                return None
            if lineage is not None:
                return lineage.translate_working_line(line_number)
            assert mapping is not None
            return mapping.get_source_line_from_target_line(line_number)

        last_source_line = None
        reannotated_lines = []

        for line in selected_lines:
            if line.kind in (" ", "+"):
                source_line = map_working_line(line.new_line_number)
                if source_line is not None:
                    last_source_line = source_line
            elif line.kind == "-":
                source_line = last_source_line
                if (
                    source_line is None
                    and line.old_line_number is not None
                    and line.old_line_number > 1
                ):
                    source_line = map_working_line(line.old_line_number - 1)
            else:
                source_line = None

            reannotated_lines.append(LineEntry(
                id=line.id,
                kind=line.kind,
                old_line_number=line.old_line_number,
                new_line_number=line.new_line_number,
                text_bytes=line.text_bytes,
                source_line=source_line,
                baseline_reference_after_line=line.baseline_reference_after_line,
                baseline_reference_after_text_bytes=line.baseline_reference_after_text_bytes,
                has_baseline_reference_after=line.has_baseline_reference_after,
                baseline_reference_before_line=line.baseline_reference_before_line,
                baseline_reference_before_text_bytes=line.baseline_reference_before_text_bytes,
                has_baseline_reference_before=line.has_baseline_reference_before,
            ))

        return reannotated_lines
    finally:
        if mapping is not None:
            mapping.close()
