"""Whole-file include-to-batch support for selected changes."""

from __future__ import annotations

import sys

from ...batch.ownership import BatchOwnership
from ...batch.storage import (
    add_binary_file_to_batch,
    add_file_to_batch,
    add_gitlink_to_batch,
)
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_text_file_deletion_hash,
)
from ...core.models import BinaryFileChange, GitlinkChange, TextFileDeletionChange
from ...core.text_lifecycle import TextFileChangeType
from ...data.file_modes import detect_file_mode
from ...data.progress import (
    record_binary_hunk_skipped,
    record_gitlink_hunk_skipped,
    record_text_deletion_hunk_skipped,
)
from ...data.session import snapshot_file_if_untracked
from ...data.text_lifecycle_detection import detect_empty_text_lifecycle_change
from ...i18n import _
from ...utils.file_io import append_lines_to_file
from ...utils.paths import get_block_list_file_path
from .action_completion import finish_selected_change_action


def save_empty_text_lifecycle_to_batch(
    batch_name: str,
    file_path: str,
    file_mode: str,
) -> str | None:
    """Persist an empty added/deleted text path, returning its lifecycle type."""
    change_type = detect_empty_text_lifecycle_change(file_path)
    if change_type is None:
        return None

    snapshot_file_if_untracked(file_path)
    add_file_to_batch(
        batch_name,
        file_path,
        BatchOwnership([], []),
        file_mode,
        change_type=change_type,
    )
    return change_type.value


def include_text_deletion_to_batch(
    batch_name: str,
    deletion_change: TextFileDeletionChange,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save one whole-text-file deletion to a batch and mark it processed."""
    file_path = deletion_change.path()
    patch_hash = compute_text_file_deletion_hash(deletion_change)

    add_file_to_batch(
        batch_name,
        file_path,
        BatchOwnership([], []),
        detect_file_mode(file_path),
        change_type=TextFileChangeType.DELETED.value,
    )
    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_text_deletion_hunk_skipped(deletion_change, patch_hash)

    if not quiet:
        print(
            _("Included text file deletion '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)


def include_binary_to_batch(
    batch_name: str,
    binary_change: BinaryFileChange,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save one binary change to a batch and mark it processed."""
    file_path = binary_change.new_path if binary_change.new_path != "/dev/null" else binary_change.old_path
    patch_hash = compute_binary_file_hash(binary_change)

    add_binary_file_to_batch(
        batch_name,
        binary_change,
        file_mode=detect_file_mode(file_path),
    )
    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_binary_hunk_skipped(binary_change, patch_hash)

    if not quiet:
        print(
            _("Included binary file '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)


def include_gitlink_to_batch(
    batch_name: str,
    gitlink_change: GitlinkChange,
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Save one submodule pointer change to a batch and mark it processed."""
    file_path = gitlink_change.path()
    patch_hash = compute_gitlink_change_hash(gitlink_change)

    add_gitlink_to_batch(batch_name, gitlink_change)
    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_gitlink_hunk_skipped(gitlink_change, patch_hash)

    if not quiet:
        print(
            _("Included submodule pointer '{file}' to batch '{batch}'").format(
                file=file_path,
                batch=batch_name,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
