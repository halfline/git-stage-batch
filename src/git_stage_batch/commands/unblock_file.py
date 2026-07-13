"""Unblock-file command implementation."""

from __future__ import annotations

import sys
from contextlib import nullcontext

from ..data.session import session_is_active
from ..data.undo_checkpoints import undo_checkpoint
from ..data.ignore_files import (
    add_pattern_to_gitignore,
    add_pattern_to_local_exclude,
    literal_ignore_pattern,
    promote_directory_to_glob_in_gitignore,
    promote_directory_to_glob_in_local_exclude,
    remove_file_from_gitignore,
    remove_file_from_local_exclude,
)
from ..exceptions import NoMoreHunks, exit_with_error
from ..i18n import _
from ..utils.file_io import (
    append_file_path_to_file,
    read_file_paths_file,
    remove_file_path_from_file,
)
from ..utils.git_command import run_git_command
from ..utils.git_index import git_add_paths
from ..utils.git_repository import (
    require_git_repository,
)
from ..utils.repository_path import normalize_repository_path
from ..utils.paths import (
    ensure_state_directory_exists,
    get_auto_added_files_file_path,
    get_blocked_files_file_path,
)
from .selection.action_completion import advance_to_and_show_next_change


def _find_covering_directory(path: str, blocked_files: list[str]) -> str | None:
    """Return the blocked directory entry that covers path, or None."""
    for entry in blocked_files:
        if entry.endswith("/") and path.startswith(entry):
            return entry
    return None


def _is_absent_from_head(file_path: str) -> bool:
    """Return True when file_path has no entry in HEAD."""
    head_check = run_git_command(
        ["cat-file", "-e", f"HEAD:{file_path}"], check=False, requires_index_lock=False
    )
    return head_check.returncode != 0


def _is_absent_from_index(file_path: str) -> bool:
    """Return True when file_path has no index entry."""
    stage_result = run_git_command(
        ["ls-files", "--stage", "--", file_path], check=False, requires_index_lock=False
    )
    return not stage_result.stdout.strip()


def command_unblock_file(file_path_arg: str) -> None:
    """Remove a file from .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for unblock-file command."))

    # Resolve to repo-relative path, normalizing directories to a trailing slash
    file_path = normalize_repository_path(file_path_arg).value
    session_active = session_is_active()
    checkpoint = (
        undo_checkpoint(
            f"unblock-file {file_path}",
            worktree_paths=[".gitignore"],
            index_paths=[file_path] if not file_path.endswith("/") else [],
        )
        if session_active
        else nullcontext()
    )

    with checkpoint:
        # Try direct removal first
        removed_from_gitignore = remove_file_from_gitignore(file_path)
        removed_from_local_exclude = remove_file_from_local_exclude(file_path)

        blocked_files = read_file_paths_file(get_blocked_files_file_path())
        directly_blocked = file_path in blocked_files
        if directly_blocked:
            remove_file_path_from_file(get_blocked_files_file_path(), file_path)

        # If not found directly, check whether a directory entry covers this path.
        # If so, promote dir/ to dir/** in the ignore file(s) and append a negation
        # so git re-includes this specific file while still ignoring the rest.
        if (
            not removed_from_gitignore
            and not removed_from_local_exclude
            and not directly_blocked
        ):
            covering_dir = _find_covering_directory(file_path, blocked_files)
            if covering_dir is not None:
                if promote_directory_to_glob_in_gitignore(covering_dir):
                    add_pattern_to_gitignore(f"!{literal_ignore_pattern(file_path)}")
                    if file_path.endswith("/"):
                        add_pattern_to_gitignore(
                            f"!{literal_ignore_pattern(file_path)}**"
                        )
                    removed_from_gitignore = True
                if promote_directory_to_glob_in_local_exclude(covering_dir):
                    add_pattern_to_local_exclude(
                        f"!{literal_ignore_pattern(file_path)}"
                    )
                    if file_path.endswith("/"):
                        add_pattern_to_local_exclude(
                            f"!{literal_ignore_pattern(file_path)}**"
                        )
                    removed_from_local_exclude = True
                append_file_path_to_file(get_blocked_files_file_path(), f"!{file_path}")

        # Re-add new untracked files as intent-to-add if session is active
        if (
            session_active
            and not file_path.endswith("/")
            and _is_absent_from_head(file_path)
            and _is_absent_from_index(file_path)
        ):
            git_add_paths([file_path], intent_to_add=True)
            append_file_path_to_file(get_auto_added_files_file_path(), file_path)

    if removed_from_gitignore or removed_from_local_exclude:
        print(f"Unblocked file: {file_path}", file=sys.stderr)
    else:
        print(
            f"Removed from blocked list: {file_path} (was not in .gitignore)",
            file=sys.stderr,
        )

    if session_active:
        try:
            advance_to_and_show_next_change()
        except NoMoreHunks:
            pass
