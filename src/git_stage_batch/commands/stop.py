"""Stop command implementation."""

from __future__ import annotations

import sys
import subprocess

from ..data.start_time_changes import (
    restore_unstaged_start_time_deletions,
    restore_unstaged_start_time_renames,
)
from ..data.session import clear_session_state, session_is_active
from ..data.session_ownership import (
    release_session_ownership,
    require_current_session_owner,
    require_no_foreign_session_owner,
)
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
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result.returncode == 1


def command_stop() -> None:
    """Stop the selected batch staging session."""
    require_git_repository()
    require_no_foreign_session_owner()
    session_owned = session_is_active()
    if session_owned:
        require_current_session_owner()

    # Undo auto-added files before clearing state
    auto_added_path = get_auto_added_files_file_path()
    if auto_added_path.exists():
        auto_added = read_file_paths_file(auto_added_path)
        reset_paths = [
            file_path
            for file_path in auto_added
            if not _path_has_staged_content(file_path)
        ]
        if reset_paths:
            git_reset_paths(reset_paths)

    restore_unstaged_start_time_renames()
    restore_unstaged_start_time_deletions()

    # Clear all session state (preserves batches and batch-sources)
    clear_session_state()
    if session_owned:
        release_session_ownership()

    print(_("✓ State cleared."), file=sys.stderr)
