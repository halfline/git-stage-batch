"""State directory path utilities."""

from __future__ import annotations

from pathlib import Path

from .git import get_git_repository_root_path


def get_state_directory_path() -> Path:
    """Get the path to the state directory for session data.

    Returns:
        Path to .git/git-stage-batch/ directory
    """
    return get_git_repository_root_path() / ".git" / "git-stage-batch"


def ensure_state_directory_exists() -> None:
    """Create the state directory if it doesn't exist."""
    get_state_directory_path().mkdir(parents=True, exist_ok=True)
