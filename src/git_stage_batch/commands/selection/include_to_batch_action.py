"""Include-to-batch command routing after live selection resolution."""

from __future__ import annotations

from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
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
from ..file_scope import include_file_to_batch as _file_scope_include_file_to_batch
from ..file_scope.target_path import require_file_scope_target_path
from . import include_line_batching as _include_line_batching
from . import selected_change_batch_staging as _selected_change_batch_staging
from . import whole_file_batch_staging as _whole_file_batch_staging


def execute_include_to_batch_action(
    *,
    batch_name: str,
    line_ids: str | None,
    file: str | None,
    original_file_scope: str | None,
    review_state,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Route one resolved include-to-batch request to the selected action."""
    validate_batch_name(batch_name)
    operation_parts = ["include", "--to", batch_name]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])

    with undo_checkpoint(" ".join(operation_parts)):
        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.MODE
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, FileModeChange):
                _whole_file_batch_staging.include_mode_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.RENAME
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, RenameChange):
                exit_with_error(
                    _(
                        "Cannot include rename '{old} -> {new}' to a batch yet. "
                        "Stage, skip, or discard the rename first."
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
            selected_change = load_selected_change()
            if isinstance(selected_change, GitlinkChange):
                _whole_file_batch_staging.include_gitlink_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            _selected_change_batch_staging.include_selected_change_to_batch(
                batch_name,
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.DELETION
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, TextFileDeletionChange):
                _whole_file_batch_staging.include_text_deletion_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            _selected_change_batch_staging.include_selected_change_to_batch(
                batch_name,
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if (
            file is None
            and line_ids is None
            and read_selected_change_kind() == SelectedChangeKind.BINARY
        ):
            selected_change = load_selected_change()
            if isinstance(selected_change, BinaryFileChange):
                _whole_file_batch_staging.include_binary_to_batch(
                    batch_name,
                    selected_change,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
                return
            _selected_change_batch_staging.include_selected_change_to_batch(
                batch_name,
                quiet=quiet,
                auto_advance=auto_advance,
            )
            return

        if file is not None:
            target_file = require_file_scope_target_path(file)
            if line_ids is None:
                _file_scope_include_file_to_batch.include_file_to_batch(
                    batch_name,
                    target_file,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
            else:
                _include_line_batching.include_file_lines_to_batch(
                    batch_name,
                    target_file,
                    line_ids,
                    quiet=quiet,
                    auto_advance=auto_advance,
                )
        elif line_ids is not None:
            _include_line_batching.include_selected_lines_to_batch(
                batch_name,
                line_ids,
                quiet=quiet,
                auto_advance=auto_advance,
            )
        else:
            _selected_change_batch_staging.include_selected_change_to_batch(
                batch_name,
                quiet=quiet,
                auto_advance=auto_advance,
            )

    if original_file_scope in (None, "") and line_ids is not None:
        finish_review_scoped_line_action(review_state)
