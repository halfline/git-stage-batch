"""Line-replacement support for discard commands."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path

from ...batch.display import annotate_with_batch_source_working_lines
from ...batch.operations import create_batch
from ...batch.ownership import (
    BatchOwnership,
    merge_batch_ownership,
    remap_batch_ownership_with_lineage,
    translate_lines_to_batch_ownership,
)
from ...batch.query import read_batch_metadata
from ...batch.replacement_line_runs import (
    ReplacementLineRun,
    derive_replacement_line_runs_from_lines,
)
from ...batch.selection import require_line_selection_in_view
from ...batch.source_advancement import advance_source_lines_preserving_existing_presence
from ...batch.source_refresh import (
    acquire_batch_ownership_update_for_selection,
    refresh_selected_lines_against_source_lines,
)
from ...batch.storage import add_file_to_batch
from ...batch.validation import batch_exists
from ...core.buffer import LineBuffer, buffer_ends_with_lf
from ...core.line_selection import parse_line_selection
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.batch_sources import (
    create_batch_source_commit,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ...data.file_modes import detect_file_mode
from ...data.file_hunk_display import build_file_hunk_from_buffer
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import (
    load_git_object_as_buffer,
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.operations import (
    build_target_working_tree_buffer_from_lines,
    build_target_working_tree_buffer_with_replaced_lines,
)
from ...utils.git_repository import get_git_repository_root_path
from . import replacement_selection


@dataclass(frozen=True)
class DiscardLineReplacementSelection:
    """Prepared replacement selection for discard-to-batch."""

    line_changes: object
    file_path: str
    working_file_path: Path
    selected_lines: list
    rewritten_line_changes: object
    rewritten_selected_lines: list
    rewritten_working_lines: Sequence[bytes]


def derive_live_replacement_line_runs(file_path: str) -> list[ReplacementLineRun]:
    """Derive file-level replacement runs for the current live file state."""
    with (
        load_git_object_as_buffer_or_empty(f"HEAD:{file_path}") as baseline_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        return derive_replacement_line_runs_from_lines(
            old_file_lines=baseline_lines,
            new_file_lines=working_lines,
        )


@contextmanager
def prepare_discard_line_replacement_selection(
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool = False,
) -> Iterator[DiscardLineReplacementSelection]:
    """Prepare rewritten line selection state for discard-to-batch."""
    line_changes = load_line_changes_from_state()
    requested_ids = set(parse_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    effective_ids = replacement_selection.expand_replacement_selection_ids(
        line_changes,
        requested_ids,
    )

    selected_lines = [
        line for line in line_changes.lines if line.id in effective_ids
    ]
    if not selected_lines:
        exit_with_error(
            _("No matching lines found for selection: {ids}").format(
                ids=line_id_specification
            )
        )

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not os.path.lexists(working_file_path):
        exit_with_error(
            _("File not found in working tree: {file}").format(
                file=line_changes.path
            )
        )

    replacement_payload = coerce_replacement_payload(replacement_text)
    try:
        with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
            rewritten_working_buffer = (
                build_target_working_tree_buffer_with_replaced_lines(
                    line_changes,
                    effective_ids,
                    replacement_payload,
                    working_lines,
                    working_has_trailing_newline=buffer_ends_with_lf(
                        working_lines
                    ),
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )
            )
    except ValueError as error:
        exit_with_error(str(error))

    with rewritten_working_buffer as rewritten_working_lines:
        rewritten_cached_lines = build_file_hunk_from_buffer(
            line_changes.path,
            rewritten_working_lines,
        )
        if rewritten_cached_lines is None:
            exit_with_error(
                _("No changes in file '{file}'.").format(file=line_changes.path)
            )
        rewritten_line_changes = annotate_with_batch_source_working_lines(
            line_changes.path,
            rewritten_cached_lines,
            rewritten_working_lines,
        )
        rewritten_selected_lines = _select_rewritten_replacement_lines(
            selected_lines,
            rewritten_line_changes,
        )
        yield DiscardLineReplacementSelection(
            line_changes=line_changes,
            file_path=line_changes.path,
            working_file_path=working_file_path,
            selected_lines=selected_lines,
            rewritten_line_changes=rewritten_line_changes,
            rewritten_selected_lines=rewritten_selected_lines,
            rewritten_working_lines=rewritten_working_lines,
        )


def build_discard_line_replacement_target_buffer(
    selection: DiscardLineReplacementSelection,
) -> LineBuffer:
    """Return the worktree buffer after removing rewritten replacement lines."""
    rewritten_selected_ids = {
        line.id for line in selection.rewritten_selected_lines if line.id is not None
    }
    return build_target_working_tree_buffer_from_lines(
        selection.rewritten_line_changes,
        rewritten_selected_ids,
        selection.rewritten_working_lines,
        working_has_trailing_newline=buffer_ends_with_lf(
            selection.rewritten_working_lines
        ),
    )


def add_discard_line_replacement_to_batch(
    batch_name: str,
    selection: DiscardLineReplacementSelection,
) -> None:
    """Persist a rewritten discard replacement selection to a batch."""
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    metadata = read_batch_metadata(batch_name)
    file_metadata = metadata.get("files", {}).get(selection.file_path)
    batch_source_commit = None

    with ExitStack() as ownership_stack:
        try:
            if file_metadata is None:
                update = ownership_stack.enter_context(
                    acquire_batch_ownership_update_for_selection(
                        batch_name=batch_name,
                        file_path=selection.file_path,
                        file_metadata=None,
                        selected_lines=selection.rewritten_selected_lines,
                    )
                )
                ownership = update.ownership_after
                batch_source_commit = update.batch_source_commit
            else:
                ownership, batch_source_commit = _merge_replacement_with_batch(
                    selection,
                    file_metadata=file_metadata,
                    ownership_stack=ownership_stack,
                )
        except ValueError as e:
            exit_with_error(
                _(
                    "Cannot discard lines to batch: batch source is stale and remapping failed.\n"
                    "File: {file}\n"
                    "Batch: {batch}\n"
                    "Error: {error}"
                ).format(file=selection.file_path, batch=batch_name, error=str(e))
            )

        if batch_source_commit is None:
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=selection.rewritten_working_lines,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)

        snapshot_file_if_untracked(selection.file_path)
        add_file_to_batch(
            batch_name,
            selection.file_path,
            ownership,
            detect_file_mode(selection.file_path),
            batch_source_commit=batch_source_commit,
        )


def _merge_replacement_with_batch(
    selection: DiscardLineReplacementSelection,
    *,
    file_metadata: dict,
    ownership_stack: ExitStack,
):
    current_batch_source = file_metadata.get("batch_source_commit")
    existing_ownership = ownership_stack.enter_context(
        BatchOwnership.acquire_for_metadata_dict(file_metadata)
    )
    old_source_buffer = load_git_object_as_buffer(
        f"{current_batch_source}:{selection.file_path}"
    )
    if old_source_buffer is None:
        exit_with_error(
            _(
                "Cannot discard lines to batch: failed to read batch source for '{file}'."
            ).format(file=selection.file_path)
        )

    with (
        old_source_buffer as old_source_lines,
        advance_source_lines_preserving_existing_presence(
            old_lines=old_source_lines,
            working_lines=selection.rewritten_working_lines,
            ownership=existing_ownership,
        ) as source_with_provenance,
    ):
        remapped_existing_ownership = remap_batch_ownership_with_lineage(
            ownership=existing_ownership,
            lineage=source_with_provenance.lineage,
        )
        refreshed_selected_lines = refresh_selected_lines_against_source_lines(
            selection.rewritten_selected_lines,
            source_lines=source_with_provenance.source_buffer,
            working_lines=(),
            lineage=source_with_provenance.lineage,
        )
        new_ownership = translate_lines_to_batch_ownership(
            refreshed_selected_lines
        )
        batch_source_commit = create_batch_source_commit(
            selection.file_path,
            file_buffer_override=source_with_provenance.source_buffer,
        )
        _record_session_batch_source(selection.file_path, batch_source_commit)
        return (
            merge_batch_ownership(remapped_existing_ownership, new_ownership),
            batch_source_commit,
        )


def _record_session_batch_source(file_path: str, batch_source_commit: str) -> None:
    batch_sources = load_session_batch_sources()
    batch_sources[file_path] = batch_source_commit
    save_session_batch_sources(batch_sources)


def _select_rewritten_replacement_lines(
    original_selected_lines: list,
    rewritten_line_changes,
) -> list:
    """Find the rewritten changed span that overlaps the original selection."""
    original_old_lines = {
        line.old_line_number
        for line in original_selected_lines
        if line.old_line_number is not None
    }
    original_new_lines = {
        line.new_line_number
        for line in original_selected_lines
        if line.new_line_number is not None
    }

    matching_indices = [
        index
        for index, line in enumerate(rewritten_line_changes.lines)
        if line.kind != " " and (
            line.old_line_number in original_old_lines
            or line.new_line_number in original_new_lines
        )
    ]
    if matching_indices:
        start_index = min(matching_indices)
        end_index = max(matching_indices)
        return [
            line
            for line in rewritten_line_changes.lines[start_index:end_index + 1]
            if line.kind != " "
        ]

    exit_with_error(
        _("Replacement selection could not be located after rewriting the file.")
    )
