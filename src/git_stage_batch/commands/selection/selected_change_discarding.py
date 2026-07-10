"""Selected-change discard support for discard commands."""

from __future__ import annotations

import os

from ...batch.submodule_pointer import discard_submodule_pointer_from_batch
from ...core.models import GitlinkChange, RenameChange, TextFileDeletionChange
from ...data.session import snapshot_file_if_untracked, snapshot_files_if_untracked
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git import (
    get_git_repository_root_path,
    git_checkout_paths,
    git_remove_paths,
    git_update_gitlink,
    git_update_index,
    run_git_command,
)


def discard_gitlink_change(gitlink_change: GitlinkChange) -> None:
    """Restore one submodule pointer change to its baseline state."""
    file_path = gitlink_change.path()
    file_meta = {
        "file_type": "gitlink",
        "change_type": gitlink_change.change_type,
        "old_oid": gitlink_change.old_oid,
        "new_oid": gitlink_change.new_oid,
    }
    discard_submodule_pointer_from_batch(file_path, file_meta)

    if gitlink_change.is_new_file():
        return
    if gitlink_change.old_oid is None:
        exit_with_error(
            _("Cannot discard submodule pointer for {file}: missing baseline commit.").format(
                file=file_path,
            )
        )
    index_result = git_update_gitlink(
        file_path=file_path,
        oid=gitlink_change.old_oid,
        check=False,
    )
    if index_result.returncode != 0:
        exit_with_error(
            _("Failed to update submodule pointer in the index for {file}: {error}").format(
                file=file_path,
                error=index_result.stderr,
            )
        )


def discard_rename_change(rename_change: RenameChange) -> None:
    """Restore the old path and remove the renamed destination."""
    snapshot_files_if_untracked([rename_change.new_path])

    remove_result = git_remove_paths(
        [rename_change.new_path],
        force=True,
        ignore_unmatch=True,
        check=False,
    )
    if remove_result.returncode != 0:
        index_result = git_update_index(
            file_path=rename_change.new_path,
            force_remove=True,
            check=False,
        )
        if index_result.returncode != 0:
            exit_with_error(
                _("Failed to remove renamed path {file}: {error}").format(
                    file=rename_change.new_path,
                    error=index_result.stderr,
                )
            )
        _remove_worktree_path(rename_change.new_path)

    restore_result = git_checkout_paths("HEAD", [rename_change.old_path], check=False)
    if restore_result.returncode != 0:
        exit_with_error(
            _("Failed to restore renamed source {file}: {error}").format(
                file=rename_change.old_path,
                error=restore_result.stderr,
            )
        )


def discard_text_deletion_change(deletion_change: TextFileDeletionChange) -> None:
    """Restore a whole-text-file path deletion from the current index."""
    file_path = deletion_change.path()
    snapshot_file_if_untracked(file_path)

    restore_result = run_git_command(
        ["checkout", "--", file_path],
        check=False,
        requires_index_lock=True,
    )
    if restore_result.returncode != 0:
        exit_with_error(
            _("Failed to restore deleted file {file}: {error}").format(
                file=file_path,
                error=restore_result.stderr,
            )
        )


def _remove_worktree_path(file_path: str) -> None:
    """Remove a working-tree path if it still exists after index cleanup."""
    absolute_path = get_git_repository_root_path() / file_path
    if not os.path.lexists(absolute_path):
        return
    if absolute_path.is_dir() and not absolute_path.is_symlink():
        # A rename diff is file-oriented, but avoid leaving an untracked directory
        # behind if the destination path was replaced while the prompt was open.
        for child in sorted(absolute_path.rglob("*"), reverse=True):
            if child.is_dir() and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
        absolute_path.rmdir()
        return
    absolute_path.unlink()
