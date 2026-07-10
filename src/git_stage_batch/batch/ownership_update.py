"""Batch ownership update preparation for selected lines."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from ..core.models import LineEntry
from .hunk_ownership_translation import translate_hunk_selection_to_batch_ownership
from .ownership import BatchOwnership
from .ownership_metadata_loading import acquire_ownership_for_metadata_dict
from .ownership_merging import merge_batch_ownership
from .ownership_translation import translate_lines_to_batch_ownership
from .replacement_line_runs import ReplacementLineRun
from .source_refresh import ensure_batch_source_current_for_selection


@dataclass
class PreparedBatchUpdate:
    """Prepared ownership update for a batch file after stale-source handling.

    This represents a complete ownership update ready to be persisted,
    including the new ownership merged with existing ownership.
    """
    batch_source_commit: str | None
    """The batch source commit to use for this file."""

    ownership_before: BatchOwnership | None
    """Ownership before applying this update, possibly remapped to new source."""

    ownership_after: BatchOwnership
    """Ownership after merging new selection with existing ownership."""


def _merge_refreshed_selected_lines_into_hunk(
    hunk_lines: Sequence[LineEntry],
    selected_lines: Sequence[LineEntry],
) -> list[LineEntry]:
    """Return full hunk lines with refreshed selected-line coordinates."""
    selected_by_id = {
        line.id: line
        for line in selected_lines
        if line.id is not None
    }
    if not selected_by_id:
        return list(hunk_lines)

    return [
        selected_by_id.get(line.id, line)
        if line.id is not None else
        line
        for line in hunk_lines
    ]


def _translate_selection_to_batch_ownership(
    selected_lines: list,
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Sequence[ReplacementLineRun] | None = None,
) -> BatchOwnership:
    """Translate a selection, using full-hunk replacement context when available."""
    selected_ids = {
        line.id
        for line in selected_lines
        if line.id is not None
    }
    if hunk_lines is not None and replacement_line_runs is not None and selected_ids:
        return translate_hunk_selection_to_batch_ownership(
            _merge_refreshed_selected_lines_into_hunk(
                hunk_lines,
                selected_lines,
            ),
            selected_ids,
            replacement_line_runs=list(replacement_line_runs),
        )

    return translate_lines_to_batch_ownership(selected_lines)


def prepare_batch_ownership_update_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Sequence[ReplacementLineRun] | None = None,
) -> PreparedBatchUpdate:
    """Prepare complete ownership update after stale-source handling."""
    refreshed = ensure_batch_source_current_for_selection(
        batch_name=batch_name,
        file_path=file_path,
        current_batch_source_commit=current_batch_source_commit,
        existing_ownership=existing_ownership,
        selected_lines=selected_lines
    )

    new_ownership = _translate_selection_to_batch_ownership(
        refreshed.selected_lines,
        hunk_lines=hunk_lines,
        replacement_line_runs=replacement_line_runs,
    )

    if refreshed.ownership:
        merged_ownership = merge_batch_ownership(refreshed.ownership, new_ownership)
    else:
        merged_ownership = new_ownership

    return PreparedBatchUpdate(
        batch_source_commit=refreshed.batch_source_commit,
        ownership_before=refreshed.ownership,
        ownership_after=merged_ownership
    )


@contextmanager
def acquire_batch_ownership_update_for_selection(
    *,
    batch_name: str,
    file_path: str,
    file_metadata: dict | None,
    selected_lines: list,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Sequence[ReplacementLineRun] | None = None,
) -> Iterator[PreparedBatchUpdate]:
    """Acquire existing ownership metadata while preparing a batch update.

    The yielded ownership may borrow deletion content from acquired metadata,
    so callers should persist or detach it before leaving the context.
    """
    if file_metadata is None:
        yield prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=None,
            existing_ownership=None,
            selected_lines=selected_lines,
            hunk_lines=hunk_lines,
            replacement_line_runs=replacement_line_runs,
        )
        return

    with acquire_ownership_for_metadata_dict(file_metadata) as existing_ownership:
        yield prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=file_metadata.get("batch_source_commit"),
            existing_ownership=existing_ownership,
            selected_lines=selected_lines,
            hunk_lines=hunk_lines,
            replacement_line_runs=replacement_line_runs,
        )
