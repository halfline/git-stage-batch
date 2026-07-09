"""Selected-change staging support for include commands."""

from __future__ import annotations

import subprocess
import sys

from ...core.buffer import LineBuffer
from ...core.diff_parser import patch_is_file_deletion
from ...core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.hunk_tracking import fetch_next_change
from ...data.index_entries import read_index_entry
from ...data.progress import record_hunk_included
from ...data.selected_change.loading import load_selected_change
from ...data.undo import undo_checkpoint
from ...exceptions import NoMoreHunks, exit_with_error
from ...i18n import _
from ...staging.index_update import update_index_with_blob_buffer
from ...utils.file_io import read_text_file_contents
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_add_paths,
    git_apply_to_index,
    git_update_gitlink,
    git_update_index,
    git_update_index_entries,
)
from ...utils.paths import (
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from .action_completion import finish_selected_change_action


def include_selected_change(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> int | None:
    """Include the currently selected change in the index."""
    item = load_selected_change()
    if item is None:
        try:
            item = fetch_next_change()
        except NoMoreHunks:
            if not quiet:
                print(_("No more hunks to process."), file=sys.stderr)
            return 0

    with undo_checkpoint("include"):
        return _include_loaded_selected_change(
            item,
            quiet=quiet,
            auto_advance=auto_advance,
        )


def _include_loaded_selected_change(
    item,
    *,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Include one loaded selected change in the index."""
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

    if isinstance(item, RenameChange):
        stage_rename_change(item)
        record_hunk_included(patch_hash)

        if not quiet:
            print(
                _("✓ Rename staged: {old} -> {new}").format(
                    old=item.old_path,
                    new=item.new_path,
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, TextFileDeletionChange):
        stage_text_deletion_change(item)
        record_hunk_included(patch_hash)

        if not quiet:
            print(
                _("✓ Text file deletion staged: {file}").format(file=item.path()),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, GitlinkChange):
        result = stage_gitlink_change(item)
        if result.returncode != 0:
            print(
                _("Failed to stage submodule pointer: {error}").format(
                    error=result.stderr,
                ),
                file=sys.stderr,
            )
            return

        record_hunk_included(patch_hash)

        if not quiet:
            print(
                _("✓ Submodule pointer {desc}: {file}").format(
                    desc=item.change_type,
                    file=item.path(),
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, BinaryFileChange):
        file_path = item.new_path if item.new_path != "/dev/null" else item.old_path
        result = git_add_paths([file_path], check=False)
        if result.returncode != 0:
            print(
                _("Failed to stage binary file: {error}").format(
                    error=result.stderr,
                ),
                file=sys.stderr,
            )
            return

        record_hunk_included(patch_hash)

        if not quiet:
            if item.is_new_file():
                change_desc = "added"
            elif item.is_deleted_file():
                change_desc = "deleted"
            else:
                change_desc = "modified"
            print(
                _("✓ Binary file {desc}: {file}").format(
                    desc=change_desc,
                    file=file_path,
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    file_path = item.path

    with LineBuffer.from_path(get_selected_hunk_patch_file_path()) as patch_buffer:
        if patch_is_file_deletion(patch_buffer):
            with LineBuffer.from_bytes(b"") as empty_buffer:
                update_index_with_blob_buffer(file_path, empty_buffer)
            apply_result = None
        else:
            apply_result = git_apply_to_index(
                patch_buffer.byte_chunks(),
                check=False,
            )

    if apply_result is not None and apply_result.returncode != 0:
        print(
            _("Failed to apply hunk: {error}").format(error=apply_result.stderr),
            file=sys.stderr,
        )
        return

    record_hunk_included(patch_hash)

    if not quiet:
        print(_("✓ Hunk staged from {file}").format(file=file_path), file=sys.stderr)

    finish_selected_change_action(
        quiet=quiet,
        auto_advance=auto_advance,
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
