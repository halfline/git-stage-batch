"""Batch-source line annotation for line-level changes."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from ...core.models import LineEntry, LineLevelChange
from ...utils.git_repository import get_git_repository_root_path
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match import match_lines
from .cache import get_session_source_hint
from .line_coordinates import translate_display_source_coordinates
from .line_coordinates import (
    IdentitySourceCoordinates,
    StructuralSourceCoordinates,
)


def _apply_batch_source_mapping(
    line_changes: LineLevelChange,
    mapping: LineMapping,
) -> LineLevelChange:
    """Apply batch source line mapping to LineLevelChange.

    Uses the mapping to translate working tree line numbers to batch source line
    numbers. For deletions, uses the last known batch source line as insertion
    position.
    """
    new_lines: list[LineEntry] = []

    for line, source_line in translate_display_source_coordinates(
        line_changes.lines,
        StructuralSourceCoordinates(mapping),
    ):
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


def annotate_with_batch_source_mapping(
    line_changes: LineLevelChange,
    mapping: LineMapping | None,
) -> LineLevelChange:
    """Annotate one hunk from a reusable batch-source mapping."""
    if mapping is None:
        return _fill_source_from_working_tree(line_changes)
    return _apply_batch_source_mapping(line_changes, mapping)


@contextmanager
def acquire_batch_source_mapping(
    path_value: str,
    *,
    batch_source_commit: str | None,
    working_lines: Sequence[bytes],
    spool_dir: str | Path | None = None,
) -> Iterator[LineMapping | None]:
    """Acquire one reusable source-to-working mapping for a repository file."""
    if not batch_source_commit:
        yield None
        return

    batch_source_buffer = read_git_object_buffer_or_none(
        f"{batch_source_commit}:{path_value}",
        spool_dir=spool_dir,
    )
    if batch_source_buffer is None:
        yield None
        return

    with (
        batch_source_buffer as batch_source_lines,
        match_lines(
            batch_source_lines,
            working_lines,
            spool_dir=spool_dir,
        ) as mapping,
    ):
        yield mapping


def _fill_source_from_working_tree(line_changes: LineLevelChange) -> LineLevelChange:
    """Fill source_line with working tree line numbers.

    Used when no batch source exists yet. The working tree will become the batch
    source when changes are saved.
    """
    new_lines: list[LineEntry] = []

    for line, source_line in translate_display_source_coordinates(
        line_changes.lines,
        IdentitySourceCoordinates(),
    ):
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
    if not os.path.lexists(file_full_path):
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
    *,
    spool_dir: str | Path | None = None,
) -> LineLevelChange:
    """Annotate LineLevelChange with indexed working content lines."""
    source_hint = get_session_source_hint(path_value)
    with acquire_batch_source_mapping(
        path_value,
        batch_source_commit=(
            None if source_hint is None else source_hint.commit
        ),
        working_lines=working_lines,
        spool_dir=spool_dir,
    ) as mapping:
        return annotate_with_batch_source_mapping(line_changes, mapping)
