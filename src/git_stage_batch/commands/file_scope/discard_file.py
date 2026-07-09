"""File-scoped discard support."""

from __future__ import annotations

import sys

from ...core.diff_parser import acquire_unified_diff
from ...core.hashing import (
    compute_binary_file_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ...core.models import BinaryFileChange, RenameChange, TextFileDeletionChange
from ...data.file_change_display import (
    render_gitlink_change,
    render_rename_change,
    render_text_deletion_change,
)
from ...data.file_tracking import auto_add_untracked_files
from ...data.live_diff import stream_live_git_diff
from ...data.progress import record_hunk_discarded
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.session import snapshot_file_if_untracked
from ...data.undo_checkpoints import undo_checkpoint
from ...i18n import _
from ...utils.file_io import append_lines_to_file, read_text_file_line_set
from ...utils.git_command import run_git_command
from ...utils.git_worktree import git_remove_paths
from ...utils.paths import get_block_list_file_path, get_context_lines
from ..selection.action_completion import finish_selected_change_action
from ..selection.selected_change_discarding import (
    discard_gitlink_change,
    discard_rename_change,
    discard_text_deletion_change,
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
    with undo_checkpoint(f"discard --file {file}".rstrip()):
        blocklist_path = get_block_list_file_path()
        blocked_hashes = read_text_file_line_set(blocklist_path)

        deletion_change = render_text_deletion_change(target_file)
        if deletion_change is not None:
            patch_hash = compute_text_file_deletion_hash(deletion_change)
            discard_text_deletion_change(deletion_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Text file deletion discarded: {file}").format(file=target_file),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        gitlink_change = render_gitlink_change(target_file)
        if gitlink_change is not None:
            patch_hash = compute_gitlink_change_hash(gitlink_change)
            discard_gitlink_change(gitlink_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Submodule pointer restored: {file}").format(file=target_file),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        rename_change = render_rename_change(target_file)
        if rename_change is not None:
            patch_hash = compute_rename_change_hash(rename_change)
            discard_rename_change(rename_change)
            if patch_hash not in blocked_hashes:
                append_lines_to_file(blocklist_path, [patch_hash])
                record_hunk_discarded(patch_hash)
            print(
                _("✓ Rename discarded: {old} -> {new}").format(
                    old=rename_change.old_path,
                    new=rename_change.new_path,
                ),
                file=sys.stderr,
            )
            finish_selected_change_action(quiet=False, auto_advance=auto_advance)
            return

        snapshot_file_if_untracked(target_file)

        hashes_to_block = []
        with acquire_unified_diff(
            stream_live_git_diff(context_lines=get_context_lines())
        ) as patches:
            for patch in patches:
                if isinstance(patch, RenameChange):
                    if target_file not in (patch.old_path, patch.new_path):
                        continue

                    patch_hash = compute_rename_change_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if isinstance(patch, TextFileDeletionChange):
                    if patch.path() != target_file:
                        continue

                    patch_hash = compute_text_file_deletion_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if isinstance(patch, BinaryFileChange):
                    file_path = patch.new_path if patch.new_path != "/dev/null" else patch.old_path
                    if file_path != target_file:
                        continue

                    patch_hash = compute_binary_file_hash(patch)
                    if patch_hash not in blocked_hashes:
                        hashes_to_block.append(patch_hash)
                    continue

                if patch.new_path != target_file:
                    continue

                patch_hash = compute_stable_hunk_hash_from_lines(patch.lines)

                if patch_hash not in blocked_hashes:
                    hashes_to_block.append(patch_hash)

        result = git_remove_paths([target_file], force=True, check=False)
        if result.returncode != 0:
            print(_("Failed to discard file: {}").format(result.stderr), file=sys.stderr)
            return

        for patch_hash in hashes_to_block:
            append_lines_to_file(blocklist_path, [patch_hash])
            record_hunk_discarded(patch_hash)

        print(_("✓ File discarded: {}").format(target_file), file=sys.stderr)

        finish_selected_change_action(quiet=False, auto_advance=auto_advance)
