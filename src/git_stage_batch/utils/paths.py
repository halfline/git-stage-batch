"""State directory path utilities."""

from __future__ import annotations

from pathlib import Path

from .file_io import read_text_file_contents
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


def get_block_list_file_path() -> Path:
    """Get the path to the blocklist file for tracking processed hunks.

    Returns:
        Path to blocklist file
    """
    return get_state_directory_path() / "blocklist"


def get_current_hunk_patch_file_path() -> Path:
    """Get the path to the current hunk patch file.

    Returns:
        Path to current hunk patch file
    """
    return get_state_directory_path() / "current-hunk-patch"


def get_current_hunk_hash_file_path() -> Path:
    """Get the path to the current hunk hash file.

    Returns:
        Path to current hunk hash file
    """
    return get_state_directory_path() / "current-hunk-hash"


def get_context_lines_file_path() -> Path:
    """Get the path to the context lines configuration file.

    Returns:
        Path to context lines file
    """
    return get_state_directory_path() / "context-lines"


def get_context_lines() -> int:
    """Get stored context lines value, defaulting to 3.

    Returns:
        Number of context lines to use in diffs
    """
    context_file = get_context_lines_file_path()
    if context_file.exists():
        try:
            return int(read_text_file_contents(context_file).strip())
        except ValueError:
            return 3
    return 3
