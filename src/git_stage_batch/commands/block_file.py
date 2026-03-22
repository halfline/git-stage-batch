"""Block-file command implementation."""

from __future__ import annotations

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import append_file_path_to_file
from ..utils.git import add_file_to_gitignore, require_git_repository, resolve_file_path_to_repo_relative
from ..utils.paths import ensure_state_directory_exists, get_blocked_files_file_path


def command_block_file(file_path_arg: str) -> None:
    """Permanently exclude a file by adding it to .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        exit_with_error(_("File path required for block-file command."))

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Add to .gitignore
    add_file_to_gitignore(file_path)

    # Add to blocked-files state
    append_file_path_to_file(get_blocked_files_file_path(), file_path)

    print(_("Blocked file: {}").format(file_path))
