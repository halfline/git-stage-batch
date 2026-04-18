"""Unblock-file command implementation."""

from __future__ import annotations

import sys
from contextlib import nullcontext

from ..data.hunk_tracking import advance_to_and_show_next_change
from ..data.undo import undo_checkpoint
from ..exceptions import NoMoreHunks, exit_with_error
from ..i18n import _
from ..utils.file_io import append_file_path_to_file, remove_file_path_from_file
from ..utils.git import remove_file_from_gitignore, require_git_repository, resolve_file_path_to_repo_relative, run_git_command
from ..utils.paths import ensure_state_directory_exists, get_abort_head_file_path, get_auto_added_files_file_path, get_blocked_files_file_path


def _is_absent_from_head(file_path: str) -> bool:
    """Return True when file_path has no entry in HEAD."""
    head_check = run_git_command(["cat-file", "-e", f"HEAD:{file_path}"], check=False)
    return head_check.returncode != 0


def _is_absent_from_index(file_path: str) -> bool:
    """Return True when file_path has no index entry."""
    stage_result = run_git_command(["ls-files", "--stage", "--", file_path], check=False)
    return not stage_result.stdout.strip()


def command_unblock_file(file_path_arg: str) -> None:
    """Remove a file from .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for unblock-file command."))

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)
    session_active = get_abort_head_file_path().exists()
    checkpoint = (
        undo_checkpoint(f"unblock-file {file_path}", worktree_paths=[".gitignore"])
        if session_active else nullcontext()
    )

    with checkpoint:
        # Remove from .gitignore
        removed_from_gitignore = remove_file_from_gitignore(file_path)

        # Remove from blocked-files state
        remove_file_path_from_file(get_blocked_files_file_path(), file_path)

        # Re-add new untracked files as intent-to-add if session is active
        if session_active and _is_absent_from_head(file_path) and _is_absent_from_index(file_path):
            result = run_git_command(["add", "-N", "--", file_path], check=False)
            if result.returncode == 0:
                append_file_path_to_file(get_auto_added_files_file_path(), file_path)

    if removed_from_gitignore:
        print(f"Unblocked file: {file_path}", file=sys.stderr)
    else:
        print(f"Removed from blocked list: {file_path} (was not in .gitignore)", file=sys.stderr)

    if session_active:
        try:
            advance_to_and_show_next_change()
        except NoMoreHunks:
            pass
