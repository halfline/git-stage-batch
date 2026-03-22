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


def get_processed_include_ids_file_path() -> Path:
    """Get the path to the processed include IDs file.

    Returns:
        Path to processed include IDs file
    """
    return get_state_directory_path() / "processed.include"


def get_processed_skip_ids_file_path() -> Path:
    """Get the path to the processed skip IDs file.

    Returns:
        Path to processed skip IDs file
    """
    return get_state_directory_path() / "processed.skip"


def get_current_lines_json_file_path() -> Path:
    """Get the path to the current lines JSON file.

    Returns:
        Path to current lines JSON file
    """
    return get_state_directory_path() / "current-lines.json"


def get_index_snapshot_file_path() -> Path:
    """Get the path to the index snapshot file.

    Returns:
        Path to index snapshot file
    """
    return get_state_directory_path() / "index-snapshot"


def get_working_tree_snapshot_file_path() -> Path:
    """Get the path to the working tree snapshot file.

    Returns:
        Path to working tree snapshot file
    """
    return get_state_directory_path() / "working-tree-snapshot"


def get_context_lines_file_path() -> Path:
    """Get the path to the context lines configuration file.

    Returns:
        Path to context lines file
    """
    return get_state_directory_path() / "context-lines"
