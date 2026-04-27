"""Reset command implementation."""

from __future__ import annotations

import json
import sys

from ..batch.operations import create_batch
from ..batch.ownership import (
    BatchOwnership,
    build_ownership_units_from_display,
    filter_ownership_units_by_display_ids,
    merge_batch_ownership,
    rebuild_ownership_from_units,
    validate_ownership_units,
)
from ..batch.query import read_batch_metadata
from ..batch.selection import (
    require_single_file_context_for_line_selection,
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
from ..data.undo import undo_checkpoint
from ..utils.file_io import write_text_file_contents
from ..utils.git import require_git_repository, run_git_command
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

    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    if to_batch is not None:
        validate_batch_name(to_batch)
        if to_batch == batch_name:
            exit_with_error(_("--to must name a different batch"))
    operation_parts = ["reset", "--from", batch_name]
    if to_batch is not None:
        operation_parts.extend(["--to", to_batch])
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    with undo_checkpoint(" ".join(operation_parts)):
        if to_batch is not None:
            _move_claims_between_batches(batch_name, to_batch, file, patterns, line_ids)
        elif file is not None:
            _reset_file_claims_from_batch(batch_name, file, line_ids)
        elif patterns is not None:
            _reset_pattern_claims_from_batch(batch_name, patterns, line_ids)
        elif line_ids is not None:
            _reset_line_claims_from_batch(batch_name, line_ids)
        else:
            _reset_all_claims_from_batch(batch_name)

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
            exit_with_error(_("Cannot merge text claims into binary file '{file}' in destination batch").format(file=file_path))
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
        exit_with_error(_("Cannot use --lines with binary files. Binary files must be reset as complete units."))

    ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])

    # Get batch source content for semantic analysis and display reconstruction
    batch_source_commit = metadata["files"][file_path]["batch_source_commit"]
    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{file_path}"],
        check=False,
        text_output=False
    )
    if batch_source_result.returncode != 0:
        exit_with_error(_("Failed to read batch source content for {file}").format(file=file_path))
    batch_source_content = batch_source_result.stdout

    remaining_units = _partition_line_ownership_units(ownership, batch_source_content, lines_to_remove)[0]

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
        )


def _select_line_ownership_for_file(
    batch_name: str,
    file_path: str,
    lines_to_select: set[int],
) -> BatchOwnership:
    """Build ownership for selected display line IDs from one batch file."""
    metadata = read_batch_metadata(batch_name)

    if metadata["files"][file_path].get("file_type") == "binary":
        exit_with_error(_("Cannot use --lines with binary files. Binary files must be reset as complete units."))

    ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])
    batch_source_commit = metadata["files"][file_path]["batch_source_commit"]
    batch_source_result = run_git_command(
        ["show", f"{batch_source_commit}:{file_path}"],
        check=False,
        text_output=False
    )
    if batch_source_result.returncode != 0:
        exit_with_error(_("Failed to read batch source content for {file}").format(file=file_path))

    _remaining_units, selected_units = _partition_line_ownership_units(
        ownership,
        batch_source_result.stdout,
        lines_to_select,
    )
    validate_ownership_units(selected_units)
    return rebuild_ownership_from_units(selected_units)


def _partition_line_ownership_units(
    ownership: BatchOwnership,
    batch_source_content: bytes,
    selected_line_ids: set[int],
):
    """Partition ownership units by selected display line IDs."""
    # This uses the actual display model, not proximity heuristics
    units = build_ownership_units_from_display(ownership, batch_source_content)

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
