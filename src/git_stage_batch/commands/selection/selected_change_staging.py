"""Selected-change staging support for include commands."""

from __future__ import annotations

import subprocess

from ...core.models import GitlinkChange, RenameChange, TextFileDeletionChange
from ...data.index_entries import read_index_entry
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git import (
    GitIndexEntryUpdate,
    git_update_gitlink,
    git_update_index,
    git_update_index_entries,
)


def stage_gitlink_change(gitlink_change: GitlinkChange) -> subprocess.CompletedProcess:
    """Stage a submodule pointer change in the index."""
    file_path = gitlink_change.path()
    if gitlink_change.is_deleted_file():
        return git_update_gitlink(
            file_path=file_path,
            oid=None,
            remove=True,
            check=False,
        )
    if gitlink_change.new_oid is None:
        exit_with_error(
            _("Cannot stage submodule pointer for {file}: missing target commit.").format(
                file=file_path,
            )
        )
    return git_update_gitlink(
        file_path=file_path,
        oid=gitlink_change.new_oid,
        check=False,
    )


def stage_rename_change(rename_change: RenameChange) -> None:
    """Stage only the structural rename, leaving destination content edits unstaged."""
    index_entry = read_index_entry(rename_change.old_path)
    if index_entry is None:
        exit_with_error(
            _("Cannot stage rename {old} -> {new}: missing baseline index entry.").format(
                old=rename_change.old_path,
                new=rename_change.new_path,
            )
        )

    git_update_index_entries(
        [
            GitIndexEntryUpdate(file_path=rename_change.old_path, force_remove=True),
            GitIndexEntryUpdate(
                file_path=rename_change.new_path,
                mode=index_entry.mode,
                blob_sha=index_entry.object_id,
            ),
        ]
    )


def stage_text_deletion_change(deletion_change: TextFileDeletionChange) -> None:
    """Stage a whole-text-file deletion in the index."""
    result = git_update_index(
        file_path=deletion_change.path(),
        force_remove=True,
        check=False,
    )
    if result.returncode != 0:
        exit_with_error(
            _("Failed to stage deletion for {file}: {error}").format(
                file=deletion_change.path(),
                error=result.stderr,
            )
        )
