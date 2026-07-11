"""Selected-change discard support for discard commands."""

from __future__ import annotations

import os
import sys

from ...batch.submodule_pointer import discard_submodule_pointer_from_batch
from ...core.buffer import LineBuffer
from ...core.diff_parser import build_line_changes_from_patch_lines, patch_is_new_file
from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.hunk_tracking import fetch_next_change
from ...data.progress import record_hunk_discarded
from ...data.selected_change.loading import load_selected_change
from ...data.selected_change.paths import worktree_paths_for_selected_change
from ...data.session import snapshot_file_if_untracked, snapshot_files_if_untracked
from ...data.file_modes import apply_git_file_mode
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import CommandError, NoMoreHunks, exit_with_error
from ...i18n import _
from ...utils.file_io import append_lines_to_file, path_is_empty, read_text_file_contents
from ...utils.git_command import run_git_command
from ...utils.git_worktree import (
    git_apply_to_worktree,
    git_checkout_paths,
    git_remove_paths,
)
from ...utils.git_index import (
    git_update_gitlink,
    git_update_index,
)
from ...utils.git_repository import get_git_repository_root_path
from ...utils.journal import log_journal
from ...utils.paths import (
    get_block_list_file_path,
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)
from .action_completion import finish_selected_change_action


def discard_selected_change(
    *,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Discard the currently selected change from the working tree."""
    try:
        item = load_selected_change()
    except CommandError as error:
        stale_message = _(
            "Cached hunk is stale (file was changed). Run 'start' or 'again' to continue."
        )
        if error.message == stale_message:
            item = None
        else:
            raise

    if item is None:
        try:
            item = fetch_next_change()
        except NoMoreHunks:
            if not quiet:
                print(_("No more hunks to process."), file=sys.stderr)
            return

    with undo_checkpoint(
        "discard",
        worktree_paths=worktree_paths_for_selected_change(item),
    ):
        _discard_loaded_selected_change(
            item,
            quiet=quiet,
            auto_advance=auto_advance,
        )


def _discard_loaded_selected_change(
    item,
    *,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Discard one loaded selected change from the working tree."""
    patch_hash = read_text_file_contents(get_selected_hunk_hash_file_path()).strip()

    if isinstance(item, FileModeChange):
        discard_file_mode_change(item)
        append_lines_to_file(get_block_list_file_path(), [patch_hash])
        record_hunk_discarded(patch_hash)
        if not quiet:
            print(_("✓ File mode discarded: {file}").format(file=item.path()), file=sys.stderr)
        finish_selected_change_action(quiet=quiet, auto_advance=auto_advance)
        return
    if isinstance(item, RenameChange):
        discard_rename_change(item)
        append_lines_to_file(get_block_list_file_path(), [patch_hash])
        record_hunk_discarded(patch_hash)

        if not quiet:
            print(
                _("✓ Rename discarded: {old} -> {new}").format(
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
        discard_text_deletion_change(item)
        append_lines_to_file(get_block_list_file_path(), [patch_hash])
        record_hunk_discarded(patch_hash)

        if not quiet:
            print(
                _("✓ Text file deletion discarded: {file}").format(
                    file=item.path(),
                ),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, GitlinkChange):
        discard_gitlink_change(item)
        append_lines_to_file(get_block_list_file_path(), [patch_hash])
        record_hunk_discarded(patch_hash)

        if not quiet:
            print(
                _("✓ Submodule pointer restored: {file}").format(file=item.path()),
                file=sys.stderr,
            )

        finish_selected_change_action(
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    if isinstance(item, BinaryFileChange):
        _discard_binary_change(
            item,
            patch_hash=patch_hash,
            quiet=quiet,
            auto_advance=auto_advance,
        )
        return

    _discard_text_hunk(
        patch_hash=patch_hash,
        quiet=quiet,
        auto_advance=auto_advance,
    )


def _discard_binary_change(
    item: BinaryFileChange,
    *,
    patch_hash: str,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Discard one selected binary change from the working tree."""
    file_path = item.path()

    if file_path != "/dev/null":
        snapshot_file_if_untracked(file_path)

    log_journal(
        "command_discard_binary_file",
        file_path=file_path,
        change_type=item.change_type,
    )

    if item.is_new_file():
        absolute_path = get_git_repository_root_path() / file_path
        if absolute_path.exists():
            absolute_path.unlink()
            log_journal("command_discard_binary_deleted", file_path=file_path)
    elif item.is_deleted_file():
        result = git_checkout_paths("HEAD", [file_path], check=False)
        if result.returncode != 0:
            print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
            return
        log_journal("command_discard_binary_restored", file_path=file_path)
    else:
        result = git_checkout_paths("HEAD", [file_path], check=False)
        if result.returncode != 0:
            print(_("Failed to restore binary file: {}").format(result.stderr), file=sys.stderr)
            return
        log_journal("command_discard_binary_restored", file_path=file_path)

    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_hunk_discarded(patch_hash)

    if not quiet:
        if item.is_new_file():
            change_desc = "added"
        elif item.is_deleted_file():
            change_desc = "deleted"
        else:
            change_desc = "modified"
        print(
            _("✓ Binary file {desc} discarded: {file}").format(
                desc=change_desc,
                file=file_path,
            ),
            file=sys.stderr,
        )

    finish_selected_change_action(
        quiet=quiet,
        auto_advance=auto_advance,
    )


def discard_file_mode_change(item: FileModeChange) -> None:
    """Restore the old executable mode in the working tree."""
    path = get_git_repository_root_path() / item.path()
    apply_git_file_mode(path, item.old_mode)


def _discard_text_hunk(
    *,
    patch_hash: str,
    quiet: bool,
    auto_advance: bool | None,
) -> None:
    """Discard one selected text hunk from the working tree."""
    with LineBuffer.from_path(get_selected_hunk_patch_file_path()) as patch_buffer:
        line_changes = build_line_changes_from_patch_lines(patch_buffer)
        file_path = line_changes.path
        is_new_file = patch_is_new_file(patch_buffer)

        if file_path != "/dev/null":
            snapshot_file_if_untracked(file_path)

        log_journal(
            "command_discard_before_git_apply",
            filename=file_path,
            patch_hash=patch_hash,
        )
        apply_result = git_apply_to_worktree(
            patch_buffer.byte_chunks(),
            reverse=True,
            check=False,
        )

    exit_code = apply_result.returncode
    stderr_text = apply_result.stderr or ""
    log_journal(
        "command_discard_after_git_apply",
        exit_code=exit_code,
        stderr_len=len(stderr_text),
        filename=file_path,
    )

    if exit_code != 0:
        log_journal(
            "command_discard_git_apply_failed",
            exit_code=exit_code,
            stderr=stderr_text,
            filename=file_path,
        )
        print(_("Failed to discard hunk: {}").format(stderr_text), file=sys.stderr)
        return

    if is_new_file:
        absolute_path = get_git_repository_root_path() / file_path
        if absolute_path.exists() and path_is_empty(absolute_path):
            absolute_path.unlink()

    append_lines_to_file(get_block_list_file_path(), [patch_hash])
    record_hunk_discarded(patch_hash)

    log_journal("command_discard_success", filename=file_path, patch_hash=patch_hash)

    if not quiet:
        print(_("✓ Hunk discarded from {file}").format(file=file_path), file=sys.stderr)

    finish_selected_change_action(
        quiet=quiet,
        auto_advance=auto_advance,
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
