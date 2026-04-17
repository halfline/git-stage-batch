"""Block-file command implementation."""

from __future__ import annotations

import sys

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import append_file_path_to_file, remove_file_path_from_file
from ..utils.paths import ensure_state_directory_exists, get_abort_head_file_path, get_auto_added_files_file_path, get_blocked_files_file_path
from ..utils.git import add_file_to_gitignore, require_git_repository, resolve_file_path_to_repo_relative, run_git_command


EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def _is_new_intent_to_add_file(file_path: str) -> bool:
    """Return True when file_path is an intent-to-add entry absent from HEAD."""
    stage_result = run_git_command(["ls-files", "--stage", "--", file_path], check=False)
    stage_output = stage_result.stdout.strip()
    if not stage_output:
        return False

    parts = stage_output.split()
    if len(parts) < 2 or parts[1] != EMPTY_BLOB_HASH:
        return False

    head_check = run_git_command(["cat-file", "-e", f"HEAD:{file_path}"], check=False)
    return head_check.returncode != 0


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

    # Remove from index if session is active and the file is a new intent-to-add entry
    if get_abort_head_file_path().exists() and _is_new_intent_to_add_file(file_path):
        run_git_command(["rm", "--cached", "--quiet", "--ignore-unmatch", "--", file_path], check=False)
        remove_file_path_from_file(get_auto_added_files_file_path(), file_path)

    # Add to blocked-files state
    append_file_path_to_file(get_blocked_files_file_path(), file_path)

    print(_("Blocked file: {}").format(file_path), file=sys.stderr)
