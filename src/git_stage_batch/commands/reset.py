"""Reset command implementation."""

from __future__ import annotations

import sys

from .batch_source import reset_claims as _reset_claims
from .batch_source import reset_selection as _reset_selection
from .batch_source import selection_state_cleanup as _selection_state_cleanup
from ..i18n import _
from ..data.undo import undo_checkpoint
from ..utils.git_repository import require_git_repository
from ..utils.paths import ensure_state_directory_exists


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
    selection = _reset_selection.resolve_reset_claim_selection(
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
        to_batch=to_batch,
    )
    batch_name = selection.batch_name
    file = selection.file
    effective_line_ids = selection.effective_line_ids

    with undo_checkpoint(" ".join(selection.operation_parts)):
        if to_batch is not None:
            _reset_claims.move_claims_between_batches(
                batch_name,
                to_batch,
                file,
                patterns,
                effective_line_ids,
            )
        elif file is not None:
            _reset_claims.reset_file_claims_from_batch(
                batch_name,
                file,
                effective_line_ids,
            )
        elif patterns is not None:
            _reset_claims.reset_pattern_claims_from_batch(
                batch_name,
                patterns,
                effective_line_ids,
            )
        elif line_ids is not None:
            _reset_claims.reset_line_claims_from_batch(
                batch_name,
                effective_line_ids,
            )
        else:
            _reset_claims.reset_all_claims_from_batch(batch_name)

    _selection_state_cleanup.clear_selected_batch_state_after_batch_mutation(
        source_batch=batch_name,
        dest_batch=to_batch,
        affected_files=selection.affected_files,
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
