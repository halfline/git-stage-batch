"""Again command implementation."""

from __future__ import annotations

import shutil

from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists, get_state_directory_path


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    state_dir = get_state_directory_path()

    if state_dir.exists():
        shutil.rmtree(state_dir)

    ensure_state_directory_exists()
