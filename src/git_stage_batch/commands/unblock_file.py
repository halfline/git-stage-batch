"""Unblock-file command implementation."""

from __future__ import annotations

import sys

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import remove_file_path_from_file
from ..utils.git import remove_file_from_gitignore, require_git_repository, resolve_file_path_to_repo_relative
from ..utils.paths import ensure_state_directory_exists, get_blocked_files_file_path


def command_unblock_file(file_path_arg: str) -> None:
    """Remove a file from .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for unblock-file command."))

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Remove from .gitignore
    removed_from_gitignore = remove_file_from_gitignore(file_path)

    # Remove from blocked-files state
    remove_file_path_from_file(get_blocked_files_file_path(), file_path)

    if removed_from_gitignore:
        print(f"Unblocked file: {file_path}", file=sys.stderr)
    else:
        print(f"Removed from blocked list: {file_path} (was not in .gitignore)", file=sys.stderr)
