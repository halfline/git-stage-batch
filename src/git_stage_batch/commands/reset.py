"""Reset command implementation."""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Sequence

from ..core.line_selection import format_line_ids
from ..batch.operations import create_batch
from ..batch.ownership import (
    BatchOwnership,
    build_ownership_units_from_batch_source_lines,
    filter_ownership_units_by_display_ids,
    merge_batch_ownership,
    rebuild_ownership_from_units,
    validate_ownership_units,
)
from ..batch.query import read_batch_metadata
from ..batch.selection import (
    require_display_ids_available,
    require_single_file_context_for_line_selection,
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
)
from ..batch.storage import (
    add_file_to_batch,
    copy_file_from_batch_to_batch,
    remove_file_from_batch,
)
from ..batch.state_refs import sync_batch_state_refs
from ..batch.validation import batch_exists, validate_batch_name
from ..exceptions import MergeError, exit_with_error
from ..i18n import _
from ..data.file_review_state import (
    FileReviewAction,
    ReviewSource,
    fresh_batch_review_selection_groups_for_action,
    read_last_file_review_state,
    resolve_batch_source_action_scope,
    validate_review_scoped_line_selection,
)
from ..data.hunk_tracking import (
    SelectedChangeKind,
    clear_selected_change_state_files,
    get_selected_change_file_path,
    mark_selected_change_cleared_by_stale_batch_selection,
    read_selected_change_kind,
    render_batch_file_display,
    selected_batch_binary_matches_batch,
)
from ..data.undo import undo_checkpoint
from ..editor import load_git_object_as_buffer
from ..utils.file_io import write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
    get_batch_metadata_file_path,
)


def command_reset_from_batch(
    batch_name: str,
    line_ids: str | None = None,
    file: str | None = None,
    patterns: list[str] | None = None,
    to_batch: str | None = None,
) -> None:
    """Remove claims from a batch, making changes visible again if not claimed elsewhere.

    Args:
        batch_name: Name of the batch to reset claims from
        line_ids: Optional line ID specification (e.g., "1,3,5-7")
        file: Optional file path to reset from batch.
              If None and line_ids is None, resets all claims in the batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        to_batch: Optional destination batch to receive the reset claims.
    """
    require_git_repository()
    ensure_state_directory_exists()
    validate_batch_name(batch_name)
    extra_action_parts = ()
    if to_batch is not None:
        extra_action_parts = ("--to", shlex.quote(to_batch))
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.RESET_FROM_BATCH,
        command_name="reset",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
        extra_action_parts=extra_action_parts,
    )
    file = scope_resolution.file

    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    metadata = read_batch_metadata(batch_name)
    all_files = metadata.get("files", {})
    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    if to_batch is not None:
        validate_batch_name(to_batch)
        if to_batch == batch_name:
            exit_with_error(_("--to must name a different batch"))

    effective_line_ids = (
        _translate_reset_line_ids_to_selection_ids(batch_name, all_files, file, patterns, line_ids)
        if line_ids is not None else
        None
    )
    affected_files = set(resolve_batch_file_scope(batch_name, all_files, file, patterns).keys())
    operation_parts = ["reset", "--from", batch_name]
    if to_batch is not None:
        operation_parts.extend(["--to", to_batch])
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if to_batch is not None:
            _move_claims_between_batches(batch_name, to_batch, file, patterns, effective_line_ids)
        elif file is not None:
            _reset_file_claims_from_batch(batch_name, file, effective_line_ids)
        elif patterns is not None:
            _reset_pattern_claims_from_batch(batch_name, patterns, effective_line_ids)
        elif line_ids is not None:
            _reset_line_claims_from_batch(batch_name, effective_line_ids)
        else:
            _reset_all_claims_from_batch(batch_name)

    _clear_selected_batch_state_after_batch_mutation(
        source_batch=batch_name,
        dest_batch=to_batch,
        affected_files=affected_files,
    )

    if to_batch is not None and line_ids:
        print(_("✓ Moved line(s) {lines} from batch '{source}' to '{dest}'").format(
            lines=line_ids, source=batch_name, dest=to_batch), file=sys.stderr)
    elif to_batch is not None and file is not None:
        print(_("✓ Moved file from batch '{source}' to '{dest}'").format(
            source=batch_name, dest=to_batch), file=sys.stderr)
    elif to_batch is not None:
        print(_("✓ Moved all claims from batch '{source}' to '{dest}'").format(
            source=batch_name, dest=to_batch), file=sys.stderr)
    elif line_ids:
        print(_("✓ Reset line(s) {lines} from batch '{name}'").format(
            lines=line_ids, name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Reset file from batch '{name}'").format(name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Reset all claims from batch '{name}'").format(name=batch_name), file=sys.stderr)


def _clear_selected_batch_state_after_batch_mutation(
    *,
    source_batch: str,
    dest_batch: str | None,
    affected_files: set[str],
) -> None:
    """Clear selected batch views that point at files changed by reset."""
    selected_kind = read_selected_change_kind()
    if selected_kind not in (SelectedChangeKind.BATCH_FILE, SelectedChangeKind.BATCH_BINARY):
        return

    selected_file = get_selected_change_file_path()
    if selected_file is None or selected_file not in affected_files:
        return

    if selected_kind == SelectedChangeKind.BATCH_BINARY:
        if selected_batch_binary_matches_batch(source_batch) or (
            dest_batch is not None and selected_batch_binary_matches_batch(dest_batch)
        ):
            stale_batch = source_batch if selected_batch_binary_matches_batch(source_batch) else dest_batch
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_stale_batch_selection(
                batch_name=stale_batch or source_batch,
                file_path=selected_file,
            )
        return

    review_state = read_last_file_review_state()
    if review_state is not None:
        if review_state.source == ReviewSource.BATCH and review_state.batch_name in {source_batch, dest_batch}:
            stale_batch = review_state.batch_name or source_batch
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_stale_batch_selection(
                batch_name=stale_batch,
                file_path=selected_file,
            )
        return

    # Filtered batch text views do not persist the batch name, so clear on a
    # matching path rather than leave a stale pathless action target behind.
    clear_selected_change_state_files()
    mark_selected_change_cleared_by_stale_batch_selection(
        batch_name=source_batch,
        file_path=selected_file,
    )


def _translate_reset_line_ids_to_selection_ids(
    batch_name: str,
    all_files: dict[str, dict],
    file: str | None,
    patterns: list[str] | None,
    line_id_specification: str,
) -> str:
    """Translate fresh file-review gutter IDs to batch selection IDs.

    Reset is a metadata operation, so explicit reset line IDs must keep working
    even when a batch change is not currently mergeable into the worktree. Only
    translate through the mergeability-filtered gutter map when a fresh batch
    file review is in scope; otherwise leave the batch display IDs untouched.
    """
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_id_specification, "reset"
    )
    if selected_ids is None:
        return line_id_specification

    file_path = list(files.keys())[0]
    if files[file_path].get("file_type") == "binary":
        exit_with_error(_("Cannot use --lines with binary files. Reset the whole file instead."))

    review_groups = fresh_batch_review_selection_groups_for_action(
        batch_name,
        file_path,
        FileReviewAction.RESET_FROM_BATCH,
    )
    if review_groups is None:
        return line_id_specification
    validate_review_scoped_line_selection(selected_ids, review_groups)

    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        exit_with_error(
            _("No changes for file '{file}' in batch '{name}'.").format(
                file=file_path,
                name=batch_name,
            )
        )

    display_id_map = rendered.review_gutter_to_selection_id or rendered.gutter_to_selection_id
    selection_ids = set()
    for gutter_id in selected_ids:
        if gutter_id in display_id_map:
            selection_ids.add(display_id_map[gutter_id])
        else:
            exit_with_error(
                _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                    id=gutter_id
                )
            )

    return format_line_ids(sorted(selection_ids))

def _ensure_destination_batch(source_batch: str, dest_batch: str, source_metadata: dict) -> None:
    """Create destination batch from source baseline, or verify compatibility."""
    source_baseline = source_metadata.get("baseline")

    if batch_exists(dest_batch):
        dest_metadata = read_batch_metadata(dest_batch)
        if dest_metadata.get("baseline") != source_baseline:
            exit_with_error(
                _("Destination batch '{dest}' has a different baseline from source batch '{source}'").format(
                    dest=dest_batch,
                    source=source_batch,
                )
            )
        return

    create_batch(dest_batch, note=_("Split from {source}").format(source=source_batch), baseline_commit=source_baseline)


def _move_claims_between_batches(
    source_batch: str,
    dest_batch: str,
    file: str | None,
    patterns: list[str] | None,
    line_id_specification: str | None,
) -> None:
    """Move selected claims from one batch to another."""
    source_metadata = read_batch_metadata(source_batch)
    files = resolve_batch_file_scope(source_batch, source_metadata.get("files", {}), file, patterns)
    _ensure_destination_batch(source_batch, dest_batch, source_metadata)

    if line_id_specification is not None:
        selected_ids = require_single_file_context_for_line_selection(
            source_batch, files, line_id_specification, "reset"
        )
        if selected_ids is None:
            return

        file_path = list(files.keys())[0]
        selected_ownership = _select_line_ownership_for_file(source_batch, file_path, selected_ids)
        _add_ownership_to_destination(dest_batch, file_path, source_metadata["files"][file_path], selected_ownership)
        _reset_line_claims_for_file(source_batch, file_path, selected_ids)
        return

    for file_path, file_meta in files.items():
        if file_meta.get("file_type") == "binary":
            dest_file_meta = read_batch_metadata(dest_batch).get("files", {}).get(file_path)
            if dest_file_meta is not None:
                exit_with_error(
                    _("Destination batch already has file '{file}'").format(file=file_path)
                )
            copy_file_from_batch_to_batch(source_batch, dest_batch, file_path)
        else:
            ownership = BatchOwnership.from_metadata_dict(file_meta)
            _add_ownership_to_destination(dest_batch, file_path, file_meta, ownership)
        remove_file_from_batch(source_batch, file_path)


def _add_ownership_to_destination(
    dest_batch: str,
    file_path: str,
    source_file_meta: dict,
    ownership: BatchOwnership,
) -> None:
    """Add selected text ownership to destination, merging with compatible claims."""
    dest_metadata = read_batch_metadata(dest_batch)
    dest_file_meta = dest_metadata.get("files", {}).get(file_path)
    batch_source_commit = source_file_meta["batch_source_commit"]

    if dest_file_meta is not None:
        if dest_file_meta.get("file_type") == "binary":
            exit_with_error(
                _("Destination batch already has a binary version of '{file}', so text changes for the same file cannot be moved there.").format(
                    file=file_path
                )
            )
        if dest_file_meta.get("batch_source_commit") != batch_source_commit:
            exit_with_error(
                _("Destination batch already has file '{file}' with a different batch source").format(
                    file=file_path
                )
            )
        existing = BatchOwnership.from_metadata_dict(dest_file_meta)
        ownership = merge_batch_ownership(existing, ownership)

    file_mode = source_file_meta.get("mode", "100644")
    add_file_to_batch(
        dest_batch,
        file_path,
        ownership,
        file_mode,
        batch_source_commit=batch_source_commit,
        change_type=source_file_meta.get("change_type"),
    )


def _reset_file_claims_from_batch(
    batch_name: str,
    file: str,
    line_id_specification: str | None = None,
) -> None:
    """Remove claims for a file, or selected line claims within that file."""
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(batch_name, metadata.get("files", {}), file)

    if line_id_specification is None:
        file_path = list(files.keys())[0]
        remove_file_from_batch(batch_name, file_path)
        return

    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_id_specification, "reset"
    )
    if selected_ids is None:
        return

    file_path = list(files.keys())[0]
    _reset_line_claims_for_file(batch_name, file_path, selected_ids)


def _reset_pattern_claims_from_batch(
    batch_name: str,
    patterns: list[str],
    line_id_specification: str | None = None,
) -> None:
    """Remove claims for files selected by gitignore-style patterns."""
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(batch_name, metadata.get("files", {}), None, patterns)

    if line_id_specification is None:
        for file_path in files:
            remove_file_from_batch(batch_name, file_path)
        return

    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_id_specification, "reset"
    )
    if selected_ids is None:
        return

    file_path = list(files.keys())[0]
    _reset_line_claims_for_file(batch_name, file_path, selected_ids)


def _reset_line_claims_from_batch(batch_name: str, line_id_specification: str) -> None:
    """Remove specific line claims from a batch using semantic ownership filtering.

    This implementation uses semantic ownership units to ensure that coupled
    claimed lines and deletion claims are removed together, preventing orphaned
    absence constraints.

    Args:
        batch_name: Name of the batch
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    metadata = read_batch_metadata(batch_name)
    files = resolve_batch_file_scope(batch_name, metadata.get("files", {}), None)
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_id_specification, "reset"
    )
    if selected_ids is None:
        return

    file_path = list(files.keys())[0]
    _reset_line_claims_for_file(batch_name, file_path, selected_ids)


def _reset_line_claims_for_file(
    batch_name: str,
    file_path: str,
    lines_to_remove: set[int],
) -> None:
    """Remove specific display line IDs from one batch file."""
    metadata = read_batch_metadata(batch_name)

    # Get current ownership for the file
    if metadata["files"][file_path].get("file_type") == "binary":
        exit_with_error(_("Cannot use --lines with binary files. Reset the whole file instead."))

    ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])

    # Get batch source lines for semantic analysis and display reconstruction
    batch_source_commit = metadata["files"][file_path]["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Failed to read batch source content for {file}").format(file=file_path))

    with batch_source_buffer as batch_source_lines:
        remaining_units = _partition_line_ownership_units(
            ownership,
            batch_source_lines,
            lines_to_remove,
            batch_name=batch_name,
            file_path=file_path,
        )[0]

    # Step 3: Validate remaining units have valid structure
    validate_ownership_units(remaining_units)

    # Step 4: Rebuild ownership from remaining units
    new_ownership = rebuild_ownership_from_units(remaining_units)

    # Step 5: Persist updated ownership or remove file if empty
    if new_ownership.is_empty():
        # No ownership remains - remove file from batch
        remove_file_from_batch(batch_name, file_path)
    else:
        # Update the batch with new ownership
        file_mode = metadata["files"][file_path].get("mode", "100644")
        add_file_to_batch(
            batch_name,
            file_path,
            new_ownership,
            file_mode,
            batch_source_commit=batch_source_commit,
            change_type=metadata["files"][file_path].get("change_type"),
        )


def _select_line_ownership_for_file(
    batch_name: str,
    file_path: str,
    lines_to_select: set[int],
) -> BatchOwnership:
    """Build ownership for selected display line IDs from one batch file."""
    metadata = read_batch_metadata(batch_name)

    if metadata["files"][file_path].get("file_type") == "binary":
        exit_with_error(_("Cannot use --lines with binary files. Reset the whole file instead."))

    ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])
    batch_source_commit = metadata["files"][file_path]["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Failed to read batch source content for {file}").format(file=file_path))

    with batch_source_buffer as batch_source_lines:
        _remaining_units, selected_units = _partition_line_ownership_units(
            ownership,
            batch_source_lines,
            lines_to_select,
            batch_name=batch_name,
            file_path=file_path,
        )
    validate_ownership_units(selected_units)
    return rebuild_ownership_from_units(selected_units)


def _partition_line_ownership_units(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
    selected_line_ids: set[int],
    *,
    batch_name: str,
    file_path: str,
):
    """Partition ownership units by selected display line IDs."""
    # This uses the actual display model, not proximity heuristics
    units = build_ownership_units_from_batch_source_lines(
        ownership,
        batch_source_lines,
    )
    available_ids = {
        display_id
        for unit in units
        for display_id in unit.display_line_ids
    }
    require_display_ids_available(
        selected_line_ids,
        available_ids,
        line_id_specification=format_line_ids(sorted(selected_line_ids)),
        file_path=file_path,
        review_command=(
            "git-stage-batch show --from "
            f"{shlex.quote(batch_name)} --file {shlex.quote(file_path)}"
        ),
    )

    # Step 2: Filter units by selected display IDs
    # This will raise MergeError if atomic units are partially selected
    try:
        return filter_ownership_units_by_display_ids(
            units, selected_line_ids
        )
    except MergeError as e:
        # Convert MergeError to user-facing error
        exit_with_error(str(e))


def _reset_all_claims_from_batch(batch_name: str) -> None:
    """Remove all claims from a batch.

    Args:
        batch_name: Name of the batch
    """
    # Clear batch metadata files section
    metadata = read_batch_metadata(batch_name)
    metadata["files"] = {}
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
    sync_batch_state_refs(batch_name)
