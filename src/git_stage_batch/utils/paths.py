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


def get_context_lines_file_path() -> Path:
    """Get the path to the context lines configuration file.

    Returns:
        Path to context lines file
    """
    return get_state_directory_path() / "context-lines"
