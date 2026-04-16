"""Reset command implementation."""

from __future__ import annotations

import json
import sys

from ..batch.ownership import (
    BatchOwnership,
    build_ownership_units_from_display,
    filter_ownership_units_by_display_ids,
    rebuild_ownership_from_units,
    validate_ownership_units,
)
from ..batch.query import read_batch_metadata
from ..batch.storage import add_file_to_batch
from ..batch.validation import batch_exists, validate_batch_name
from ..core.line_selection import parse_line_selection
from ..data.hunk_tracking import require_selected_hunk
from ..data.line_state import load_line_changes_from_state
from ..exceptions import MergeError, exit_with_error
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git import require_git_repository, run_git_command
from ..utils.paths import (
    ensure_state_directory_exists,
    get_batch_metadata_file_path,
)


def command_reset_from_batch(batch_name: str, line_ids: str | None = None) -> None:
    """Remove claims from a batch, making changes visible again if not claimed elsewhere.

    Args:
        batch_name: Name of the batch to reset claims from
        line_ids: Optional line ID specification (e.g., "1,3,5-7")
    """
    require_git_repository()
    ensure_state_directory_exists()
    validate_batch_name(batch_name)

    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    if line_ids is not None:
        _reset_line_claims_from_batch(batch_name, line_ids)
    else:
        _reset_all_claims_from_batch(batch_name)

    if line_ids:
        print(_("✓ Reset line(s) {lines} from batch '{name}'").format(
            lines=line_ids, name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Reset all claims from batch '{name}'").format(name=batch_name), file=sys.stderr)


def _reset_line_claims_from_batch(batch_name: str, line_id_specification: str) -> None:
    """Remove specific line claims from a batch using semantic ownership filtering.

    This implementation uses semantic ownership units to ensure that coupled
    claimed lines and deletion claims are removed together, preventing orphaned
    absence constraints.

    Args:
        batch_name: Name of the batch
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    # Require cached hunk to know which file to reset lines from
    # Note: This provides the file context, but semantic analysis is based on
    # ownership structure, not ephemeral UI state
    require_selected_hunk()
    line_changes = load_line_changes_from_state()
    file_path = line_changes.path

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    if file_path not in metadata.get("files", {}):
        exit_with_error(_("File {file} not found in batch '{name}'").format(
            file=file_path, name=batch_name))

    # Parse line IDs to remove
    lines_to_remove = set(parse_line_selection(line_id_specification))

    # Get current ownership for the file
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

    # Step 1: Build semantic ownership units from reconstructed display
    # This uses the actual display model, not proximity heuristics
    units = build_ownership_units_from_display(ownership, batch_source_content)

    # Step 2: Filter units by selected display IDs
    # This will raise MergeError if atomic units are partially selected
    try:
        remaining_units, removed_units = filter_ownership_units_by_display_ids(
            units, lines_to_remove
        )
    except MergeError as e:
        # Convert MergeError to user-facing error
        exit_with_error(str(e))

    # Step 3: Validate remaining units have valid structure
    validate_ownership_units(remaining_units)

    # Step 4: Rebuild ownership from remaining units
    new_ownership = rebuild_ownership_from_units(remaining_units)

    # Step 5: Persist updated ownership or remove file if empty
    if new_ownership.is_empty():
        # No ownership remains - remove file from batch
        del metadata["files"][file_path]
        metadata_path = get_batch_metadata_file_path(batch_name)
        write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
    else:
        # Update the batch with new ownership
        file_mode = metadata["files"][file_path].get("mode", "100644")
        add_file_to_batch(batch_name, file_path, new_ownership, file_mode)


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
