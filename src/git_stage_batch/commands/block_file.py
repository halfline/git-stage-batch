"""Block-file command implementation."""

from __future__ import annotations

import sys

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import append_file_path_to_file
from ..utils.git import add_file_to_gitignore, require_git_repository, resolve_file_path_to_repo_relative
from ..utils.paths import ensure_state_directory_exists, get_blocked_files_file_path


def command_block_file(file_path_arg: str = "") -> None:
    """Permanently exclude a file by adding it to .gitignore and blocked list."""
    require_git_repository()
    ensure_state_directory_exists()

    if not file_path_arg:
        from ..data.line_state import load_line_changes_from_state
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        file_path_arg = line_changes.path

    # Resolve to repo-relative path
    file_path = resolve_file_path_to_repo_relative(file_path_arg)

    # Add to .gitignore
    add_file_to_gitignore(file_path)

    # Add to blocked-files state
    append_file_path_to_file(get_blocked_files_file_path(), file_path)

    print(_("Blocked file: {}").format(file_path), file=sys.stderr)
