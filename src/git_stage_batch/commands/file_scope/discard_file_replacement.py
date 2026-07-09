"""File-scoped discard replacement support."""

from __future__ import annotations

from contextlib import ExitStack
import sys

from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_review.state import clear_last_file_review_state_if_file_matches
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    restore_selected_change_state,
    snapshot_selected_change_state,
)
from ...data.session import snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git_repository import get_git_repository_root_path
from ..selection import discard_file_selection as _discard_file_selection
from ..selection.selected_hunk_refresh import recalculate_selected_hunk_for_command


def discard_file_as_replacement(
    replacement_text: str | ReplacementPayload,
    file: str | None,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Replace one live file-scoped working-tree file with explicit text."""
    replacement_payload = coerce_replacement_payload(replacement_text)
    operation_parts = ["discard", "--as", replacement_payload.display_text or "<stdin>"]
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

        line_changes = _discard_file_selection.load_explicit_file_selection(target_file)
        snapshot_file_if_untracked(target_file)

        absolute_path = get_git_repository_root_path() / target_file
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(replacement_payload.data)

        if preserve_selected_state:
            assert saved_selected_state is not None
            restore_selected_change_state(saved_selected_state)
        else:
            recalculate_selected_hunk_for_command(
                line_changes.path,
                auto_advance=auto_advance,
            )
        clear_last_file_review_state_if_file_matches(target_file)

    print(_("✓ Discarded file as replacement: {file}").format(file=target_file), file=sys.stderr)
