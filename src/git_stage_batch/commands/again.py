"""Again command implementation."""

from __future__ import annotations

import shutil

from ..data.file_tracking import auto_add_untracked_files
from ..data.session import require_session_started
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists, get_state_directory_path


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    require_session_started()
    state_dir = get_state_directory_path()

    if state_dir.exists():
        shutil.rmtree(state_dir)

    ensure_state_directory_exists()

    # Auto-add untracked files for fresh pass
    auto_add_untracked_files()
