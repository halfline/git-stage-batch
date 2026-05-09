"""Batch source refresh and stale-source repair.

This module provides the single authoritative path for handling stale batch sources.
Command code should use these helpers rather than manually coordinating source
advancement, ownership remapping, cache updates, and line re-annotation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import LineEntry
from ..data.batch_sources import load_session_batch_sources, save_session_batch_sources
from .match import match_lines
from .ownership import (
    BatchOwnership,
    advance_batch_source_for_file_with_provenance,
    detect_stale_batch_source_for_selection,
    merge_batch_ownership,
    translate_lines_to_batch_ownership,
)


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
    # Detect if source is stale
    is_stale = detect_stale_batch_source_for_selection(selected_lines)

    if is_stale and current_batch_source_commit and existing_ownership:
        # Batch source is stale - advance it and remap existing ownership
        advance_result = advance_batch_source_for_file_with_provenance(
            batch_name=batch_name,
            file_path=file_path,
            old_batch_source_commit=current_batch_source_commit,
            existing_ownership=existing_ownership
        )

        # Update session cache so add_file_to_batch uses the new source.
        batch_sources = load_session_batch_sources()
        batch_sources[file_path] = advance_result.batch_source_commit
        save_session_batch_sources(batch_sources)

        # Re-annotate lines against the actual advanced source. The advanced
        # source may include already-owned lines that are absent from the
        # working tree after earlier discard operations.
        reannotated_lines = _refresh_selected_lines_against_source_content(
            selected_lines,
            source_content=advance_result.source_content,
            working_content=advance_result.working_content,
            working_line_map=advance_result.working_line_map,
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
        reannotated_lines = _refresh_selected_lines_against_new_source(selected_lines)

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


def _refresh_selected_lines_against_new_source(selected_lines: list) -> list:
    """Re-annotate selected lines for a first-time batch source.

    This helper is only used before a batch source exists.
    The initial batch source commit will be created from the same working tree
    snapshot that the selected_lines were derived from, with no transformations
    applied. This means working tree line N in the snapshot maps to batch source
    line N in the new source commit.

    This invariant is maintained by create_batch_source_commit(), which creates
    the first source from the current working tree state. Advanced batch sources
    use _refresh_selected_lines_against_source_content() instead because they
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
            text=line.text,
            source_line=source_line,
            baseline_reference_after_line=line.baseline_reference_after_line,
            baseline_reference_after_text_bytes=line.baseline_reference_after_text_bytes,
            has_baseline_reference_after=line.has_baseline_reference_after,
            baseline_reference_before_line=line.baseline_reference_before_line,
            baseline_reference_before_text_bytes=line.baseline_reference_before_text_bytes,
            has_baseline_reference_before=line.has_baseline_reference_before,
        ))

    return reannotated_lines


def _refresh_selected_lines_against_source_content(
    selected_lines: list,
    *,
    source_content: bytes,
    working_content: bytes,
    working_line_map: dict[int, int] | None = None,
) -> list:
    """Re-annotate selected lines against a concrete source snapshot.

    If a working-line provenance map is supplied, it describes how the source
    content was synthesized and is treated as authoritative. Text matching is
    used only when no provenance map is available.
    """
    mapping = None
    if working_line_map is None:
        source_lines = source_content.splitlines(keepends=True)
        working_lines = working_content.splitlines(keepends=True)
        mapping = match_lines(source_lines, working_lines)

    def map_working_line(line_number: int | None) -> int | None:
        if line_number is None:
            return None
        if working_line_map is not None:
            return working_line_map.get(line_number)
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
            text=line.text,
            source_line=source_line,
            baseline_reference_after_line=line.baseline_reference_after_line,
            baseline_reference_after_text_bytes=line.baseline_reference_after_text_bytes,
            has_baseline_reference_after=line.has_baseline_reference_after,
            baseline_reference_before_line=line.baseline_reference_before_line,
            baseline_reference_before_text_bytes=line.baseline_reference_before_text_bytes,
            has_baseline_reference_before=line.has_baseline_reference_before,
        ))

    return reannotated_lines


def prepare_batch_ownership_update_for_selection(
    batch_name: str,
    file_path: str,
    current_batch_source_commit: str | None,
    existing_ownership: BatchOwnership | None,
    selected_lines: list,
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
    new_ownership = translate_lines_to_batch_ownership(refreshed.selected_lines)

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
