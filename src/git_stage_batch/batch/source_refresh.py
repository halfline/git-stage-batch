"""Batch source refresh and stale-source repair.

This module provides the single authoritative path for handling stale batch sources.
Command code should use these helpers rather than manually coordinating source
advancement, ownership remapping, cache updates, and line re-annotation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .source_cache import (
    load_session_batch_sources,
    save_session_batch_sources,
)
from .source_snapshots import create_batch_source_commit
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from .ownership.model import (
    BatchOwnership,
)
from .ownership.translation import (
    detect_stale_batch_source_for_selection,
)
from .selected_line_source_refresh import (
    refresh_selected_lines_against_new_source as _refresh_lines_against_new_source,
    refresh_selected_lines_against_source_lines as _refresh_lines_against_source,
    selected_lines_fit_source as _selection_matches_source,
)
from .source_advancement import advance_batch_source_for_file_with_provenance


@dataclass
class RefreshedBatchSelection:
    """Selection state after ensuring batch source is current for a file.

    This represents the state after stale-source detection and repair.
    If the source was stale, it has been advanced, ownership remapped,
    and lines re-annotated. If not stale, this contains the original state.
    """
    batch_source_commit: str | None
    """The current batch source commit (possibly newly created)."""

    ownership: BatchOwnership | None
    """Existing ownership, possibly remapped to new source space."""

    selected_lines: list
    """Selected lines, possibly re-annotated for new source."""

    source_was_advanced: bool
    """True if batch source was advanced during this operation."""


def ensure_batch_source_current_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
) -> RefreshedBatchSelection:
    """Ensure batch source is current for selection, advancing if needed.

    This is the single authoritative helper for stale-source repair.

    Process:
    1. Detects whether selected lines require stale-source repair
    2. If not stale, returns original values unchanged
    3. If stale:
       - Creates new batch source commit for the file
       - Remaps ownership to the new source space
       - Updates session batch-source cache
       - Re-annotates selected lines against the new source
       - Returns a single structured result object

    The session cache is always updated correctly if source advancement occurs.
    Command code does not need to manually coordinate these steps.

    Args:
        batch_name: Name of the batch being updated
        file_path: Path to the file
        current_batch_source_commit: Current batch source commit (or None)
        existing_ownership: Existing ownership for this file (or None)
        selected_lines: LineEntry objects being added to batch

    Returns:
        RefreshedBatchSelection with current source state

    Raises:
        ValueError: If stale source remapping fails
    """
    if current_batch_source_commit is None:
        cached_source_result = _prepare_initial_cached_source_for_selection(
            file_path,
            selected_lines,
        )
        if cached_source_result is not None:
            batch_source_commit, prepared_selected_lines, source_was_advanced = (
                cached_source_result
            )
            return RefreshedBatchSelection(
                batch_source_commit=batch_source_commit,
                ownership=existing_ownership,
                selected_lines=prepared_selected_lines,
                source_was_advanced=source_was_advanced,
            )

    # Detect if source is stale.
    is_stale = detect_stale_batch_source_for_selection(selected_lines)

    if is_stale and current_batch_source_commit and existing_ownership:
        # Batch source is stale - advance it and remap existing ownership
        with advance_batch_source_for_file_with_provenance(
            batch_name=batch_name,
            file_path=file_path,
            old_batch_source_commit=current_batch_source_commit,
            existing_ownership=existing_ownership
        ) as advance_result:
            # Update session cache so add_file_to_batch uses the new source.
            batch_sources = load_session_batch_sources()
            batch_sources[file_path] = advance_result.batch_source_commit
            save_session_batch_sources(batch_sources)

            # Re-annotate lines against the refreshed source. That source may
            # include already-owned lines that are absent from the
            # working tree after earlier discard operations.
            reannotated_lines = _refresh_lines_against_source(
                selected_lines,
                source_lines=advance_result.source_buffer,
                working_lines=(),
                lineage=advance_result.lineage,
            )

            return RefreshedBatchSelection(
                batch_source_commit=advance_result.batch_source_commit,
                ownership=advance_result.ownership,
                selected_lines=reannotated_lines,
                source_was_advanced=True
            )

    elif is_stale and current_batch_source_commit and not existing_ownership:
        # Inconsistent state: batch source exists but no ownership
        # This should not happen in normal operation
        raise ValueError(
            f"Batch source exists for {file_path} in batch {batch_name} "
            f"but no ownership found. This indicates corrupted batch state."
        )

    elif is_stale and not current_batch_source_commit:
        # First time - stale is normal. The batch source will be created from
        # the same working tree snapshot, so annotate now in that coordinate
        # space before translating ownership.
        reannotated_lines = _refresh_lines_against_new_source(selected_lines)

        return RefreshedBatchSelection(
            batch_source_commit=current_batch_source_commit,
            ownership=existing_ownership,
            selected_lines=reannotated_lines,
            source_was_advanced=False
        )

    else:
        # Source is current - no changes needed
        return RefreshedBatchSelection(
            batch_source_commit=current_batch_source_commit,
            ownership=existing_ownership,
            selected_lines=selected_lines,
            source_was_advanced=False
        )


def _cache_session_source(file_path: str, batch_source_commit: str) -> None:
    batch_sources = load_session_batch_sources()
    batch_sources[file_path] = batch_source_commit
    save_session_batch_sources(batch_sources)


def _create_current_working_source_for_selection(
    file_path: str,
    selected_lines: list,
) -> tuple[str, list]:
    with load_working_tree_file_as_buffer(file_path) as working_lines:
        batch_source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=working_lines,
        )
    _cache_session_source(file_path, batch_source_commit)
    return (
        batch_source_commit,
        _refresh_lines_against_new_source(selected_lines),
    )


def _prepare_initial_cached_source_for_selection(
    file_path: str,
    selected_lines: list,
) -> tuple[str, list, bool] | None:
    batch_source_commit = load_session_batch_sources().get(file_path)
    if not batch_source_commit:
        return None

    source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if source_buffer is None:
        raise ValueError(
            f"Cannot read cached batch source for {file_path} at "
            f"{batch_source_commit}"
        )

    with source_buffer as source_lines:
        if _selection_matches_source(selected_lines, source_lines):
            return batch_source_commit, selected_lines, False

    new_batch_source_commit, reannotated_lines = (
        _create_current_working_source_for_selection(file_path, selected_lines)
    )
    return new_batch_source_commit, reannotated_lines, True
