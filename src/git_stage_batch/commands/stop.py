"""Stop command implementation."""

from __future__ import annotations

import sys

from ..data.staged_renames import restore_unstaged_start_time_deletions, restore_unstaged_start_time_renames
from ..data.session import clear_session_state
from ..i18n import _
from ..utils.file_io import read_file_paths_file
from ..utils.git_command import run_git_command
from ..utils.git_index import git_reset_paths
from ..utils.git_repository import require_git_repository
from ..utils.paths import get_auto_added_files_file_path


def _path_has_staged_content(file_path: str) -> bool:
    result = run_git_command(
        ["diff", "--cached", "--quiet", "--no-renames", "--", file_path],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 1


def command_stop() -> None:
    """Stop the selected batch staging session."""
    require_git_repository()

    # Undo auto-added files before clearing state
    auto_added_path = get_auto_added_files_file_path()
    if auto_added_path.exists():
        auto_added = read_file_paths_file(auto_added_path)
        for file_path in auto_added:
            if _path_has_staged_content(file_path):
                continue
            git_reset_paths([file_path], check=False)

    restore_unstaged_start_time_renames()
    restore_unstaged_start_time_deletions()

    # Clear all session state (preserves batches and batch-sources)
    clear_session_state()

    print(_("✓ State cleared."), file=sys.stderr)
