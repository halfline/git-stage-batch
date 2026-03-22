"""Auto-add untracked files management."""

from __future__ import annotations

from ..utils.file_io import append_file_path_to_file, read_file_paths_file
from ..utils.git import run_git_command
from ..utils.paths import get_auto_added_files_file_path


def auto_add_untracked_files() -> None:
    """Automatically run git add -N on untracked files (except blocked ones).

    This makes untracked files visible to git diff without staging their
    content, enabling the interactive staging workflow for new files.
    Files matching .gitignore patterns are automatically excluded.
    """
    # Get list of untracked files
    result = run_git_command(["ls-files", "--others", "--exclude-standard"], check=False)
    if result.returncode != 0:
        return

    untracked_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not untracked_files:
        return

    # Get already auto-added files to avoid redundant git add -N
    auto_added_path = get_auto_added_files_file_path()
    auto_added_files = set(read_file_paths_file(auto_added_path))

    # Add untracked files that haven't been auto-added yet
    for file_path in untracked_files:
        if file_path not in auto_added_files:
            result = run_git_command(["add", "-N", file_path], check=False)
            if result.returncode == 0:
                append_file_path_to_file(auto_added_path, file_path)
