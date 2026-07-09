"""File-scoped include replacement support."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_change_status import (
    file_has_staged_changes,
    file_has_unstaged_changes,
)
from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.line_id_files import write_line_ids_file
from ...data.file_review.state import clear_last_file_review_state_if_file_matches
from ...data.file_tracking import auto_add_untracked_files
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.index_update import update_index_with_blob_buffer
from ...utils.paths import get_processed_include_ids_file_path
from ..selection.selected_hunk_refresh import recalculate_selected_hunk_for_command


def include_file_as_replacement(
    replacement_text: str | ReplacementPayload,
    file: str | None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Stage full-file replacement text for a live file-scoped selection."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_text = replacement_payload.display_text
    operation_parts = ["include", "--as", operation_text or "<stdin>"]
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)), ExitStack() as selected_state_stack:
        preserve_selected_state = False
        saved_selected_state = None

        if file is None or file == "":
            target_file = get_selected_change_file_path()
            if target_file is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            target_file = file
            preserve_selected_state = True
            saved_selected_state = selected_state_stack.enter_context(
                snapshot_selected_change_state()
            )
        auto_add_untracked_files([target_file])

        if preserve_selected_state:
            line_changes = cache_unstaged_file_as_single_hunk(target_file)
            if line_changes is None and not file_has_staged_changes(target_file):
                exit_with_error(_("No changes in file '{file}'.").format(file=target_file))
        else:
            if not file_has_unstaged_changes(target_file):
                exit_with_error(_("No changes in file '{file}'.").format(file=target_file))
            line_changes = load_line_changes_from_state()
            if line_changes is None or line_changes.path != target_file:
                line_changes = cache_unstaged_file_as_single_hunk(target_file)
                if line_changes is None:
                    exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

        with LineBuffer.from_bytes(replacement_payload.data) as replacement_buffer:
            update_index_with_blob_buffer(target_file, replacement_buffer)

        if preserve_selected_state:
            assert saved_selected_state is not None
            restore_selected_change_state(saved_selected_state)
        else:
            write_line_ids_file(get_processed_include_ids_file_path(), set())
            recalculate_selected_hunk_for_command(
                target_file,
                auto_advance=auto_advance,
            )
        clear_last_file_review_state_if_file_matches(target_file)

    print(_("✓ Included file as replacement: {file}").format(file=target_file), file=sys.stderr)
