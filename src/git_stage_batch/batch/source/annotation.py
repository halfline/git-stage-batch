"""Batch-source line annotation for line-level changes."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.models import LineEntry, LineLevelChange
from ...utils.git_repository import get_git_repository_root_path
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from .cache import get_batch_source_for_file


def _apply_batch_source_mapping(
    line_changes: LineLevelChange,
    mapping: LineMapping,
) -> LineLevelChange:
    """Apply batch source line mapping to LineLevelChange.

    Uses the mapping to translate working tree line numbers to batch source line
    numbers. For deletions, uses the last known batch source line as insertion
    position.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in line_changes.lines:
        source_line = None

        if line.kind in {" ", "+"}:
            if line.new_line_number is not None:
                source_line = mapping.get_source_line_from_target_line(
                    line.new_line_number
                )
            if source_line is not None:
                last_source_line = source_line

        elif line.kind == "-":
            source_line = last_source_line
            if (
                source_line is None
                and line.old_line_number is not None
                and line.old_line_number > 1
            ):
                source_line = mapping.get_source_line_from_target_line(
                    line.old_line_number - 1
                )

        new_lines.append(
            LineEntry(
                id=line.id,
                kind=line.kind,
                old_line_number=line.old_line_number,
                new_line_number=line.new_line_number,
                text_bytes=line.text_bytes,
                source_line=source_line,
                baseline_reference_after_line=line.baseline_reference_after_line,
                baseline_reference_after_text_bytes=(
                    line.baseline_reference_after_text_bytes
                ),
                has_baseline_reference_after=line.has_baseline_reference_after,
                baseline_reference_before_line=line.baseline_reference_before_line,
                baseline_reference_before_text_bytes=(
                    line.baseline_reference_before_text_bytes
                ),
                has_baseline_reference_before=line.has_baseline_reference_before,
                has_trailing_newline=line.has_trailing_newline,
            )
        )

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=new_lines,
    )


def _fill_source_from_working_tree(line_changes: LineLevelChange) -> LineLevelChange:
    """Fill source_line with working tree line numbers.

    Used when no batch source exists yet. The working tree will become the batch
    source when changes are saved.
    """
    last_source_line: int | None = None
    new_lines: list[LineEntry] = []

    for line in line_changes.lines:
        source_line = None

        if line.kind in {" ", "+"}:
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

        new_lines.append(
            LineEntry(
                id=line.id,
                kind=line.kind,
                old_line_number=line.old_line_number,
                new_line_number=line.new_line_number,
                text_bytes=line.text_bytes,
                source_line=source_line,
                baseline_reference_after_line=line.baseline_reference_after_line,
                baseline_reference_after_text_bytes=(
                    line.baseline_reference_after_text_bytes
                ),
                has_baseline_reference_after=line.has_baseline_reference_after,
                baseline_reference_before_line=line.baseline_reference_before_line,
                baseline_reference_before_text_bytes=(
                    line.baseline_reference_before_text_bytes
                ),
                has_baseline_reference_before=line.has_baseline_reference_before,
                has_trailing_newline=line.has_trailing_newline,
            )
        )

    return LineLevelChange(
        path=line_changes.path,
        header=line_changes.header,
        lines=new_lines,
    )


def annotate_with_batch_source(
    path_value: str,
    line_changes: LineLevelChange,
) -> LineLevelChange:
    """Annotate LineLevelChange with batch source line numbers.

    This reads the working tree and batch source content, computes a line
    mapping, and populates source_line fields on LineEntry objects.

    If batch source doesn't exist, uses working tree line numbers as source_line
    since the working tree will become the batch source.
    """
    repo_root = get_git_repository_root_path()
    file_full_path = repo_root / path_value
    if not file_full_path.exists():
        return _fill_source_from_working_tree(line_changes)

    with load_working_tree_file_as_buffer(path_value) as working_lines:
        return annotate_with_batch_source_working_lines(
            path_value,
            line_changes,
            working_lines,
        )


def annotate_with_batch_source_working_lines(
    path_value: str,
    line_changes: LineLevelChange,
    working_lines: Sequence[bytes],
) -> LineLevelChange:
    """Annotate LineLevelChange with indexed working content lines."""
    batch_source_commit = get_batch_source_for_file(path_value)
    if not batch_source_commit:
        return _fill_source_from_working_tree(line_changes)

    batch_source_buffer = read_git_object_buffer_or_none(
        f"{batch_source_commit}:{path_value}"
    )
    if batch_source_buffer is None:
        return _fill_source_from_working_tree(line_changes)

    with batch_source_buffer as batch_source_lines:
        return annotate_with_batch_source_lines(
            line_changes,
            batch_source_lines=batch_source_lines,
            working_lines=working_lines,
        )


def annotate_with_batch_source_lines(
    line_changes: LineLevelChange,
    *,
    batch_source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
) -> LineLevelChange:
    """Annotate LineLevelChange from indexed batch-source and working lines."""
    with match_lines(batch_source_lines, working_lines) as mapping:
        return _apply_batch_source_mapping(line_changes, mapping)
