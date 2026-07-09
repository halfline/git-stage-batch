"""Batch source refresh and stale-source repair.

This module provides the single authoritative path for handling stale batch sources.
Command code should use these helpers rather than manually coordinating source
advancement, ownership remapping, cache updates, and line re-annotation.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from ..core.models import LineEntry
from ..data.batch_sources import (
    create_batch_source_commit,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from .lineage import BatchSourceLineage
from .match import match_lines
from .ownership import (
    BatchOwnership,
    merge_batch_ownership,
)
from .ownership_translation import (
    detect_stale_batch_source_for_selection,
    translate_hunk_selection_to_batch_ownership,
    translate_lines_to_batch_ownership,
)
from .replacement_line_runs import ReplacementLineRun
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


@dataclass
class PreparedBatchUpdate:
    """Prepared ownership update for a batch file after stale-source handling.

    This represents a complete ownership update ready to be persisted,
    including the new ownership merged with existing ownership.
    """
    batch_source_commit: str | None
    """The batch source commit to use for this file."""

    ownership_before: BatchOwnership | None
    """Ownership before applying this update (possibly remapped to new source)."""

    ownership_after: BatchOwnership
    """Ownership after merging new selection with existing ownership."""


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
            reannotated_lines = refresh_selected_lines_against_source_lines(
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
        reannotated_lines = refresh_selected_lines_against_new_source(selected_lines)

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


def _line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


def _selected_lines_fit_source(
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
        refresh_selected_lines_against_new_source(selected_lines),
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
        if _selected_lines_fit_source(selected_lines, source_lines):
            return batch_source_commit, selected_lines, False

    new_batch_source_commit, reannotated_lines = (
        _create_current_working_source_for_selection(file_path, selected_lines)
    )
    return new_batch_source_commit, reannotated_lines, True


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
    - Context/addition line: working tree line N → batch source line N
    - Deletion line: uses last known source line as anchor

    Args:
        selected_lines: LineEntry objects with potentially stale source_line values

    Returns:
        New list of LineEntry objects with refreshed source_line values
    """
    last_source_line = None
    reannotated_lines = []

    for line in selected_lines:
        if line.kind in (' ', '+'):
            # Context or addition: use working tree line number as source line
            source_line = line.new_line_number
            if source_line is not None:
                last_source_line = source_line
        elif line.kind == '-':
            source_line = last_source_line
            if source_line is None and line.old_line_number is not None and line.old_line_number > 1:
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
            if line.kind in (' ', '+'):
                source_line = map_working_line(line.new_line_number)
                if source_line is not None:
                    last_source_line = source_line
            elif line.kind == '-':
                source_line = last_source_line
                if source_line is None and line.old_line_number is not None and line.old_line_number > 1:
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
    """Prepare complete ownership update for batch file, handling stale sources.

    This is the high-level orchestration helper for include/discard-to-batch.
    It coordinates:
    1. Ensuring batch source is current (may advance source and remap ownership)
    2. Translating selected lines to new ownership
    3. Merging new ownership with existing ownership
    4. Returning a prepared update ready for persistence

    Command code stays focused on line selection and working tree updates,
    while this helper handles all batch-ownership coordination.

    Args:
        batch_name: Name of the batch being updated
        file_path: Path to the file
        current_batch_source_commit: Current batch source commit (or None)
        existing_ownership: Existing ownership for this file (or None)
        selected_lines: LineEntry objects being added to batch

    Returns:
        PreparedBatchUpdate ready to be persisted

    Raises:
        ValueError: If stale source remapping or translation fails
    """
    # Step 1: Ensure batch source is current, handling stale sources
    refreshed = ensure_batch_source_current_for_selection(
        batch_name=batch_name,
        file_path=file_path,
        current_batch_source_commit=current_batch_source_commit,
        existing_ownership=existing_ownership,
        selected_lines=selected_lines
    )

    # Step 2: Translate selected lines to new ownership
    new_ownership = _translate_selection_to_batch_ownership(
        refreshed.selected_lines,
        hunk_lines=hunk_lines,
        replacement_line_runs=replacement_line_runs,
    )

    # Step 3: Merge with existing ownership
    if refreshed.ownership:
        merged_ownership = merge_batch_ownership(refreshed.ownership, new_ownership)
    else:
        merged_ownership = new_ownership

    # Step 4: Return prepared update
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

    with BatchOwnership.acquire_for_metadata_dict(file_metadata) as existing_ownership:
        yield prepare_batch_ownership_update_for_selection(
            batch_name=batch_name,
            file_path=file_path,
            current_batch_source_commit=file_metadata.get("batch_source_commit"),
            existing_ownership=existing_ownership,
            selected_lines=selected_lines,
            hunk_lines=hunk_lines,
            replacement_line_runs=replacement_line_runs,
        )
