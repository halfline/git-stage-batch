"""Reset command implementation."""

from __future__ import annotations

import json
import shlex
import sys

from .batch_source import reset_claims as _reset_claims
from .batch_source import selection_state_cleanup as _selection_state_cleanup
from ..batch.query import read_batch_metadata
from ..batch.selection import (
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
)
from ..batch.source_selector import require_plain_batch_name
from ..batch.validation import batch_exists, validate_batch_name
from ..exceptions import exit_with_error
from ..i18n import _
from ..data.file_review.batch_selection import (
    translate_reset_batch_file_gutter_ids_to_selection_ranges,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.state import (
    resolve_batch_source_action_scope,
)
from ..data.undo import undo_checkpoint
from ..utils.git import require_git_repository
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
    batch_name = require_plain_batch_name(batch_name, "reset")
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
        translate_reset_batch_file_gutter_ids_to_selection_ranges(
            batch_name,
            all_files,
            file,
            patterns,
            line_ids,
        )
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
