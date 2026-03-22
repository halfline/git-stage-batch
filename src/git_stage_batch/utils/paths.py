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


def get_line_changes_json_file_path() -> Path:
    """Get the path to the selected lines JSON file.

    Returns:
        Path to selected lines JSON file
    """
    return get_state_directory_path() / "selected-lines.json"


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


def get_block_list_file_path() -> Path:
    """Get the path to the blocklist file for tracking processed hunks.

    Returns:
        Path to blocklist file
    """
    return get_state_directory_path() / "blocklist"


def get_selected_hunk_patch_file_path() -> Path:
    """Get the path to the selected hunk patch file.

    Returns:
        Path to selected hunk patch file
    """
    return get_state_directory_path() / "selected-hunk-patch"


def get_selected_hunk_hash_file_path() -> Path:
    """Get the path to the selected hunk hash file.

    Returns:
        Path to selected hunk hash file
    """
    return get_state_directory_path() / "selected-hunk-hash"


def get_abort_head_file_path() -> Path:
    """Get the path to the abort HEAD file for session restoration.

    Returns:
        Path to abort HEAD file
    """
    return get_state_directory_path() / "abort-head"


def get_abort_stash_file_path() -> Path:
    """Get the path to the abort stash file for session restoration.

    Returns:
        Path to abort stash file
    """
    return get_state_directory_path() / "abort-stash"


def get_abort_snapshots_directory_path() -> Path:
    """Get the path to the abort snapshots directory.

    Returns:
        Path to snapshots directory
    """
    return get_state_directory_path() / "snapshots"


def get_abort_snapshot_list_file_path() -> Path:
    """Get the path to the abort snapshot list file.

    Returns:
        Path to snapshot list file
    """
    return get_state_directory_path() / "snapshot-list"


def get_auto_added_files_file_path() -> Path:
    """Get the path to the auto-added files list file.

    Returns:
        Path to auto-added files list file
    """
    return get_state_directory_path() / "auto-added-files"


def get_blocked_files_file_path() -> Path:
    """Get the path to the blocked files list file.

    Returns:
        Path to blocked files list file
    """
    return get_state_directory_path() / "blocked-files"


def get_iteration_count_file_path() -> Path:
    """Get the path to the iteration count file.

    Returns:
        Path to iteration count file
    """
    return get_state_directory_path() / "iteration-count"


def get_start_head_file_path() -> Path:
    """Get the path to file storing HEAD SHA at session start.

    Returns:
        Path to start HEAD file
    """
    return get_state_directory_path() / "start-head"


def get_start_index_tree_file_path() -> Path:
    """Get the path to file storing index tree SHA at session start.

    Returns:
        Path to start index tree file
    """
    return get_state_directory_path() / "start-index-tree"


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


def get_included_hunks_file_path() -> Path:
    """Get the path to the included hunks file.

    Returns:
        Path to included hunks file
    """
    return get_state_directory_path() / "included-hunks"


def get_skipped_hunks_jsonl_file_path() -> Path:
    """Get the path to the skipped hunks JSONL file.

    Returns:
        Path to skipped hunks JSONL file
    """
    return get_state_directory_path() / "skipped-hunks.jsonl"


def get_discarded_hunks_file_path() -> Path:
    """Get the path to the discarded hunks file.

    Returns:
        Path to discarded hunks file
    """
    return get_state_directory_path() / "discarded-hunks"
