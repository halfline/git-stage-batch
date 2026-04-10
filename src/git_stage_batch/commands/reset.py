"""Reset command implementation."""

from __future__ import annotations

import sys

from ..batch.mask import recompute_global_batch_mask
from ..batch.validation import batch_exists, validate_batch_name
from ..core.line_selection import parse_line_selection
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
    get_batch_claimed_hunks_file_path,
)


def command_reset_from_batch(batch_name: str, line_ids: str | None = None) -> None:
    """Remove claims from a batch, unmasking hunks not claimed elsewhere.

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

    # Recompute global mask - hunks only in this batch are now unmasked
    recompute_global_batch_mask()

    if line_ids:
        print(_("✓ Reset line(s) {lines} from batch '{name}'").format(
            lines=line_ids, name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Reset all claims from batch '{name}'").format(name=batch_name), file=sys.stderr)


def _reset_line_claims_from_batch(batch_name: str, line_id_specification: str) -> None:
    """Remove specific line claims from a batch.

    Args:
        batch_name: Name of the batch
        line_id_specification: Line ID specification (e.g., "1,3,5-7")
    """
    import json
    from ..batch.query import read_batch_metadata
    from ..batch.ownership import BatchOwnership
    from ..batch.storage import add_file_to_batch
    from ..core.line_selection import format_line_ids
    from ..utils.paths import get_batch_metadata_file_path
    from ..data.hunk_tracking import require_current_hunk_and_check_stale
    from ..data.line_state import load_current_lines_from_state

    # Require cached hunk to know which file to reset lines from
    require_current_hunk_and_check_stale()
    current_lines = load_current_lines_from_state()
    file_path = current_lines.path

    # Read batch metadata
    metadata = read_batch_metadata(batch_name)
    if file_path not in metadata.get("files", {}):
        exit_with_error(_("File {file} not found in batch '{name}'").format(
            file=file_path, name=batch_name))

    # Parse line IDs to remove
    lines_to_remove = set(parse_line_selection(line_id_specification))

    # Get current ownership for the file
    ownership = BatchOwnership.from_metadata_dict(metadata["files"][file_path])

    # Collect all claimed lines and remove the specified ones
    all_claimed_ids = set()
    for range_str in ownership.claimed_lines:
        all_claimed_ids.update(parse_line_selection(range_str))

    # Remove the specified lines
    remaining_ids = all_claimed_ids - lines_to_remove

    # Format back into range strings
    new_claimed_lines = [format_line_ids(list(remaining_ids))] if remaining_ids else []

    # Create updated ownership
    new_ownership = BatchOwnership(
        claimed_lines=new_claimed_lines,
        deletions=ownership.deletions  # Keep deletions unchanged
    )

    # If no ownership remains, remove the file from batch
    if not new_ownership.claimed_lines and not new_ownership.insertions:
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
    # Clear hunk claims
    claimed_hunks_path = get_batch_claimed_hunks_file_path(batch_name)
    if claimed_hunks_path.exists():
        write_text_file_contents(claimed_hunks_path, "")

    # Clear batch metadata files section
    from ..batch.query import read_batch_metadata
    from ..utils.paths import get_batch_metadata_file_path
    import json

    metadata = read_batch_metadata(batch_name)
    metadata["files"] = {}
    metadata_path = get_batch_metadata_file_path(batch_name)
    write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))
