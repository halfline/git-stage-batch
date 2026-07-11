"""Discard-to-batch command routing after live selection resolution."""

from __future__ import annotations

from ...core.models import BinaryFileChange, FileModeChange, RenameChange, TextFileDeletionChange
from ...data.file_review.action_scope import finish_review_scoped_line_action
from ...data.selected_change.loading import load_selected_change
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ...data.undo_checkpoints import undo_checkpoint
from ...batch.validation import validate_batch_name
from ...exceptions import exit_with_error
from ...i18n import _
from ..file_scope import discard_file_to_batch as _file_scope_discard_file_to_batch
from ..file_scope.target_path import (
    checkpoint_paths_for_file_scope,
    require_file_scope_target_path,
)
from . import discard_line_batching as _discard_line_batching
from . import selected_change_batch_discarding as _selected_change_batch_discarding
from . import whole_file_batch_discarding as _whole_file_batch_discarding


def execute_discard_to_batch_action(
    *,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
    original_file_scope: str | None,
    review_state,
    quiet: bool,
    advance: bool,
    auto_advance: bool | None,
) -> int:
    """Route one resolved discard-to-batch request to the selected action."""
    validate_batch_name(batch_name)
    operation_parts = ["discard", "--to", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])

    selected_change = load_selected_change() if file is None else None
    worktree_paths = checkpoint_paths_for_file_scope(file, selected_change)
    with undo_checkpoint(" ".join(operation_parts), worktree_paths=worktree_paths):
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.MODE
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, FileModeChange):
                return _whole_file_batch_discarding.discard_mode_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.RENAME
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, RenameChange):
                exit_with_error(
                    _(
                        "Cannot discard rename '{old} -> {new}' to a batch yet. "
                        "Discard, skip, or stage the rename first."
                    ).format(
                        old=selected_change.old_path,
                        new=selected_change.new_path,
                    )
                )

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.GITLINK
        ):
            exit_with_error(
                _("Discarding submodule pointer changes to a batch is not supported yet.")
            )

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                saved_hunks = _whole_file_batch_discarding.discard_binary_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                saved_hunks = (
                    _selected_change_batch_discarding.discard_selected_change_to_batch(
                        batch_name,
                        file_only=False,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                )
        elif (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.DELETION
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, TextFileDeletionChange):
                saved_hunks = (
                    _whole_file_batch_discarding.discard_text_deletion_to_batch(
                        batch_name,
                        selected_change,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                )
            else:
                saved_hunks = (
                    _selected_change_batch_discarding.discard_selected_change_to_batch(
                        batch_name,
                        file_only=False,
                        quiet=quiet,
                        auto_advance=auto_advance,
                    )
                )
        elif file is not None:
            target_file = require_file_scope_target_path(file)
            if line_ids is None:
                saved_hunks = _file_scope_discard_file_to_batch.discard_file_to_batch(
                    batch_name,
                    target_file,
                    quiet=quiet,
                    advance=advance,
                    auto_advance=auto_advance,
                )
            else:
                saved_hunks = _discard_line_batching.discard_file_lines_to_batch(
                    batch_name,
                    target_file,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        elif line_ids is not None:
            saved_hunks = _discard_line_batching.discard_selected_lines_to_batch(
                batch_name,
                line_ids,
                quiet=quiet,
                auto_advance=auto_advance,
            )
        else:
            saved_hunks = (
                _selected_change_batch_discarding.discard_selected_change_to_batch(
                    batch_name,
                    file_only=False,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            )

    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
    return saved_hunks
