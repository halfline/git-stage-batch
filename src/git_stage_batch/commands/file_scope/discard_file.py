"""File-scoped discard support."""

from __future__ import annotations

import shlex
import sys

from ...core.diff_parser import acquire_unified_diff
from ...core.hashing import (
    compute_binary_file_hash,
    compute_file_mode_change_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...data.file_tracking import auto_add_untracked_files
from ...data.index_entries import read_index_entry
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_discarded
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.session import path_is_intent_to_add, snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.file_io import append_lines_to_file, read_text_file_line_set
from ...utils.git_command import run_git_command
from ...utils.git_index import git_update_index
from ...utils.git_repository import get_git_repository_root_path
from ...utils.git_worktree import git_checkout_index_paths
from ...utils.paths import get_block_list_file_path, get_context_lines
from ..selection.action_completion import finish_selected_change_action
from .target_path import checkpoint_paths_for_live_file
from ..selection.selected_change_discarding import (
    discard_gitlink_change,
    discard_rename_change,
)


def _discard_worktree_path_to_index(file_path: str) -> None:
    """Restore one working-tree path from the index without changing staged work."""
    index_entry = read_index_entry(file_path)
    is_intent_to_add = path_is_intent_to_add(file_path)
    if index_entry is not None and not is_intent_to_add:
        result = git_checkout_index_paths([file_path], check=False)
        if result.returncode != 0:
            exit_with_error(
                _("Failed to discard file: {}").format(result.stderr)
            )
        return

    absolute_path = get_git_repository_root_path() / file_path
    if absolute_path.exists() or absolute_path.is_symlink():
        absolute_path.unlink()
    if is_intent_to_add:
        result = git_update_index(
            file_path=file_path,
            force_remove=True,
            check=False,
        )
        if result.returncode != 0:
            exit_with_error(
                _("Failed to discard file: {}").format(result.stderr)
            )


def _print_discard_file_result(file_path: str) -> None:
    """Explain the non-deleting whole-file discard contract."""
    removal_command = shlex.join(["git", "rm", "--", file_path])
    print(
        _(
            "✓ Unstaged changes discarded from {file}. "
            "To remove the file, use `{command}`."
        ).format(file=file_path, command=removal_command),
        file=sys.stderr,
    )


def discard_file_changes(
    file: str,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Discard all changes from one resolved file scope."""
    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            diff_result = run_git_command(
                [
                    "-c",
                    "diff.ignoreSubmodules=none",
                    "diff",
                    "--ignore-submodules=none",
                    "--quiet",
                ],
                check=False,
                requires_index_lock=False,
            )
            if diff_result.returncode == 0:
                print(_("No more hunks to process."), file=sys.stderr)
            else:
                print(_("No selected hunk. Run 'show' first or specify file path."), file=sys.stderr)
            return
    else:
        target_file = file

    auto_add_untracked_files([target_file])
    checkpoint_paths = checkpoint_paths_for_live_file(target_file)
    with undo_checkpoint(
        f"discard --file {file}".rstrip(),
        worktree_paths=checkpoint_paths,
        rollback_on_error=True,
    ):
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        hashes_to_block: list[str] = []
        matching_changes = 0
        rename_change: RenameChange | None = None
        gitlink_change: GitlinkChange | None = None
        with acquire_unified_diff(
            stream_live_git_diff(
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
                context_lines=get_context_lines(),
            )
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue
                    rename_change = patch
                    patch_hash = compute_rename_change_hash(patch)
                elif isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue
                    patch_hash = compute_text_file_deletion_hash(patch)
                elif isinstance(patch, BinaryFileChange):
                    if patch.path() != target_file:
                        continue
                    patch_hash = compute_binary_file_hash(patch)
                elif isinstance(patch, GitlinkChange):
                    if patch.path() != target_file:
                        continue
                    gitlink_change = patch
                    patch_hash = compute_gitlink_change_hash(patch)
                elif isinstance(patch, FileModeChange):
                    if patch.path() != target_file:
                        continue
                    patch_hash = compute_file_mode_change_hash(patch)
                else:
                    patch_paths = {
                        path
                        for path in (patch.old_path, patch.new_path)
                        if path != "/dev/null"
                    }
                    if target_file not in patch_paths:
                        continue
                    patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                matching_changes += 1
                if patch_hash not in blocked_hashes:
                    hashes_to_block.append(patch_hash)

        if matching_changes == 0:
            print(
                _("No unstaged changes in file '{file}' to discard.").format(
                    file=target_file,
                ),
                file=sys.stderr,
            )
            return

        snapshot_file_if_untracked(target_file)
        if rename_change is not None:
            discard_rename_change(rename_change)
        elif gitlink_change is not None:
            discard_gitlink_change(gitlink_change)
        else:
            _discard_worktree_path_to_index(target_file)

        for patch_hash in hashes_to_block:
            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_discarded(patch_hash)

        _print_discard_file_result(target_file)

        finish_selected_change_action(quiet=False, auto_advance=auto_advance)
