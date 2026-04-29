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


def get_session_lock_file_path() -> Path:
    """Get the path to the session lock file.

    Returns:
        Path to session lock file
    """
    return get_state_directory_path() / "session.lock"


def ensure_state_directory_exists() -> None:
    """Create the state directory if it doesn't exist."""
    get_state_directory_path().mkdir(parents=True, exist_ok=True)


def get_session_directory_path() -> Path:
    """Get the directory containing active session scratch state."""
    path = get_state_directory_path() / "session"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_selected_state_directory_path() -> Path:
    """Get the directory containing the selected change cache."""
    path = get_session_directory_path() / "selected"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_progress_state_directory_path() -> Path:
    """Get the directory containing hunk progress state."""
    path = get_session_directory_path() / "progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_processed_state_directory_path() -> Path:
    """Get the directory containing processed line-id state."""
    path = get_session_directory_path() / "processed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_state_directory_path() -> Path:
    """Get the directory containing session configuration state."""
    path = get_session_directory_path() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_abort_state_directory_path() -> Path:
    """Get the directory containing abort/recovery state."""
    path = get_session_directory_path() / "abort"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_fixup_state_directory_path() -> Path:
    """Get the directory containing suggest-fixup state."""
    path = get_session_directory_path() / "fixup"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_processed_include_ids_file_path() -> Path:
    """Get the path to the processed include IDs file.

    Returns:
        Path to processed include IDs file
    """
    return get_processed_state_directory_path() / "included-lines.json"


def get_processed_skip_ids_file_path() -> Path:
    """Get the path to the processed skip IDs file.

    Returns:
        Path to processed skip IDs file
    """
    return get_processed_state_directory_path() / "skipped-lines.json"


def get_processed_batch_ids_file_path() -> Path:
    """Get the path to the processed batch IDs file.

    Returns:
        Path to processed batch IDs file
    """
    return get_processed_state_directory_path() / "batched-lines.json"


def get_line_changes_json_file_path() -> Path:
    """Get the path to the selected lines JSON file.

    Returns:
        Path to selected lines JSON file
    """
    return get_selected_state_directory_path() / "hunk.lines.json"


def get_index_snapshot_file_path() -> Path:
    """Get the path to the index snapshot file.

    Returns:
        Path to index snapshot file
    """
    return get_selected_state_directory_path() / "index.snapshot"


def get_working_tree_snapshot_file_path() -> Path:
    """Get the path to the working tree snapshot file.

    Returns:
        Path to working tree snapshot file
    """
    return get_selected_state_directory_path() / "working-tree.snapshot"


def get_block_list_file_path() -> Path:
    """Get the path to the blocklist file for tracking processed hunks.

    Returns:
        Path to blocklist file
    """
    return get_progress_state_directory_path() / "blocked-hunks.txt"


def get_selected_hunk_patch_file_path() -> Path:
    """Get the path to the selected hunk patch file.

    Returns:
        Path to selected hunk patch file
    """
    return get_selected_state_directory_path() / "hunk.patch"


def get_selected_hunk_hash_file_path() -> Path:
    """Get the path to the selected hunk hash file.

    Returns:
        Path to selected hunk hash file
    """
    return get_selected_state_directory_path() / "hunk.hash.txt"


def get_selected_change_kind_file_path() -> Path:
    """Get the path to the selected change kind marker file."""
    return get_selected_state_directory_path() / "change-kind.txt"


def get_selected_binary_file_json_path() -> Path:
    """Get the path to the selected binary file JSON file.

    When the selected item is a binary file (not a text hunk), this file stores
    the BinaryFileChange information as JSON.

    Returns:
        Path to selected binary file JSON file
    """
    return get_selected_state_directory_path() / "binary-file.json"


def get_abort_head_file_path() -> Path:
    """Get the path to the abort HEAD file for session restoration.

    Returns:
        Path to abort HEAD file
    """
    return get_abort_state_directory_path() / "head.txt"


def get_abort_stash_file_path() -> Path:
    """Get the path to the abort stash file for session restoration.

    Returns:
        Path to abort stash file
    """
    return get_abort_state_directory_path() / "stash.txt"


def get_abort_snapshots_directory_path() -> Path:
    """Get the path to the abort snapshots directory.

    Returns:
        Path to snapshots directory
    """
    return get_abort_state_directory_path() / "untracked"


def get_abort_snapshot_list_file_path() -> Path:
    """Get the path to the abort snapshot list file.

    Returns:
        Path to snapshot list file
    """
    return get_abort_state_directory_path() / "untracked-paths.txt"


def get_session_batch_sources_file_path() -> Path:
    """Get the path to the session batch sources cache file.

    Returns:
        Path to session-batch-sources.json file
    """
    return get_session_directory_path() / "batch-sources.json"


def get_session_consumed_selections_file_path() -> Path:
    """Get the path to the hidden consumed-selection ownership cache.

    Returns:
        Path to session-consumed-selections.json file
    """
    return get_session_directory_path() / "consumed-selections.json"


def get_auto_added_files_file_path() -> Path:
    """Get the path to the auto-added files list file.

    Returns:
        Path to auto-added files list file
    """
    return get_abort_state_directory_path() / "auto-added-files.txt"


def get_blocked_files_file_path() -> Path:
    """Get the path to the blocked files list file.

    Returns:
        Path to blocked files list file
    """
    return get_progress_state_directory_path() / "blocked-files.txt"


def get_iteration_count_file_path() -> Path:
    """Get the path to the iteration count file.

    Returns:
        Path to iteration count file
    """
    return get_config_state_directory_path() / "iteration-count.txt"


def get_start_head_file_path() -> Path:
    """Get the path to file storing HEAD SHA at session start.

    Returns:
        Path to start HEAD file
    """
    return get_session_directory_path() / "start-head.txt"


def get_start_index_tree_file_path() -> Path:
    """Get the path to file storing index tree SHA at session start.

    Returns:
        Path to start index tree file
    """
    return get_session_directory_path() / "start-index-tree.txt"


def get_start_batch_refs_file_path() -> Path:
    """Get the path to file storing batch refs at session start.

    Returns:
        Path to start batch refs file (JSON format: {batch_name: commit_sha})
    """
    return get_session_directory_path() / "start-batch-refs.json"


def get_context_lines_file_path() -> Path:
    """Get the path to the context lines configuration file.

    Returns:
        Path to context lines file
    """
    return get_config_state_directory_path() / "context-lines.txt"


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


def get_suggest_fixup_state_file_path() -> Path:
    """Get the path to the suggest-fixup state file.

    Returns:
        Path to suggest-fixup state JSON file
    """
    return get_fixup_state_directory_path() / "state.json"


def get_included_hunks_file_path() -> Path:
    """Get the path to the included hunks file.

    Returns:
        Path to included hunks file
    """
    return get_progress_state_directory_path() / "included-hunks.txt"


def get_skipped_hunks_jsonl_file_path() -> Path:
    """Get the path to the skipped hunks JSONL file.

    Returns:
        Path to skipped hunks JSONL file
    """
    return get_progress_state_directory_path() / "skipped-hunks.jsonl"


def get_discarded_hunks_file_path() -> Path:
    """Get the path to the discarded hunks file.

    Returns:
        Path to discarded hunks file
    """
    return get_progress_state_directory_path() / "discarded-hunks.txt"


def get_batched_hunks_file_path() -> Path:
    """Get the path to the batched hunks file.

    Returns:
        Path to batched hunks file
    """
    return get_progress_state_directory_path() / "batched-hunks.txt"


def get_batches_directory_path() -> Path:
    """Get the directory containing batch metadata.

    Returns:
        Path to batches directory
    """
    return get_state_directory_path() / "batches"


def get_batch_directory_path(batch_name: str) -> Path:
    """Get the directory for a specific batch's metadata.

    Args:
        batch_name: Name of the batch

    Returns:
        Path to batch directory
    """
    return get_batches_directory_path() / batch_name


def get_batch_metadata_file_path(batch_name: str) -> Path:
    """Get the metadata file path for a specific batch.

    Args:
        batch_name: Name of the batch

    Returns:
        Path to batch metadata JSON file
    """
    return get_batch_directory_path(batch_name) / "metadata.json"


def get_batch_claimed_hunks_file_path(batch_name: str) -> Path:
    """Get the claimed hunks file path for a specific batch.

    Args:
        batch_name: Name of the batch

    Returns:
        Path to batch's claimed hunks file
    """
    return get_batch_directory_path(batch_name) / "claimed_hunks"


def get_batch_claimed_line_ids_file_path(batch_name: str) -> Path:
    """Get the claimed line IDs file path for a specific batch.

    Args:
        batch_name: Name of the batch

    Returns:
        Path to batch's claimed line IDs file
    """
    return get_batch_directory_path(batch_name) / "claimed_line_ids"


def get_batch_refs_snapshot_file_path() -> Path:
    """Get the batch refs snapshot file path (for abort functionality).

    Returns:
        Path to batch refs snapshot JSON file
    """
    return get_abort_state_directory_path() / "batch-refs.json"
