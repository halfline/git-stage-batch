"""Reset command implementation."""

from __future__ import annotations

import sys

from ..batch.mask import recompute_global_batch_mask
from ..batch.validation import batch_exists, validate_batch_name
from ..core.line_selection import parse_line_selection, read_line_ids_file, write_line_ids_file
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
    get_batch_claimed_hunks_file_path,
    get_batch_claimed_line_ids_file_path,
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
    requested_ids = set(parse_line_selection(line_id_specification))
    claimed_line_ids_path = get_batch_claimed_line_ids_file_path(batch_name)

    if not claimed_line_ids_path.exists():
        # No line claims to reset
        return

    selected_claims = set(read_line_ids_file(claimed_line_ids_path))
    remaining_claims = selected_claims - requested_ids

    # Write back remaining claims
    write_line_ids_file(claimed_line_ids_path, remaining_claims)


def _reset_all_claims_from_batch(batch_name: str) -> None:
    """Remove all claims from a batch.

    Args:
        batch_name: Name of the batch
    """
    # Clear hunk claims
    claimed_hunks_path = get_batch_claimed_hunks_file_path(batch_name)
    if claimed_hunks_path.exists():
        write_text_file_contents(claimed_hunks_path, "")

    # Clear line claims
    claimed_line_ids_path = get_batch_claimed_line_ids_file_path(batch_name)
    if claimed_line_ids_path.exists():
        write_line_ids_file(claimed_line_ids_path, set())
