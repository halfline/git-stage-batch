"""Whole-file discard-to-batch support for selected changes."""

from __future__ import annotations

import sys

from ...batch.ownership import BatchOwnership
from ...batch.storage import add_binary_file_to_batch, add_file_to_batch
from ...core.hashing import compute_binary_file_hash, compute_text_file_deletion_hash
from ...core.models import BinaryFileChange, TextFileDeletionChange
from ...core.text_lifecycle import TextFileChangeType
from ...data.file_modes import detect_file_mode
from ...data.progress import record_hunk_discarded
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import append_lines_to_file
from ...utils.git import (
    get_git_repository_root_path,
    git_checkout_paths,
    git_remove_paths,
)
from ...utils.paths import get_block_list_file_path
from .action_completion import finish_selected_change_action
from .selected_change_discarding import discard_text_deletion_change


def _discard_binary_change_from_working_tree(binary_change: BinaryFileChange) -> None:
    """Discard one live binary change from the working tree."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    absolute_path = get_git_repository_root_path() / file_path

    if binary_change.is_new_file():
        if absolute_path.exists():
            absolute_path.unlink()
        git_remove_paths([file_path], cached=True, quiet=True, check=False)
        return

    result = git_checkout_paths("HEAD", [file_path], check=False)
    if result.returncode != 0:
        exit_with_error(_("Failed to restore binary file: {error}").format(error=result.stderr))


def discard_binary_to_batch(
    batch_name: str,
    binary_change: BinaryFileChange,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Save one binary change to a batch, then discard it from the working tree."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    patch_hash = compute_binary_file_hash(binary_change)

    snapshot_file_if_untracked(file_path)
    add_binary_file_to_batch(
        batch_name,
        binary_change,
        file_mode=detect_file_mode(file_path),
    )
    _discard_binary_change_from_working_tree(binary_change)

    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_hunk_discarded(patch_hash)

    if not quiet:
        print(
            _("Discarded binary file '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    if advance:
        finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
    return 1


def discard_text_deletion_to_batch(
    batch_name: str,
    deletion_change: TextFileDeletionChange,
    *,
    quiet: bool = False,
    advance: bool = True,
    auto_advance: bool | None = None,
) -> int:
    """Save one whole-text-file deletion to a batch, then restore it locally."""
    file_path = deletion_change.path()
    patch_hash = compute_text_file_deletion_hash(deletion_change)

    snapshot_file_if_untracked(file_path)
    add_file_to_batch(
        batch_name,
        file_path,
        BatchOwnership([], []),
        detect_file_mode(file_path),
        change_type=TextFileChangeType.DELETED.value,
    )
    discard_text_deletion_change(deletion_change)

    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_hunk_discarded(patch_hash)

    if not quiet:
        print(
            _("Discarded text file deletion '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    if advance:
        finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
    return 1
