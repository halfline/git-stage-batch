"""Session state management for iteration tracking and abort support."""

from __future__ import annotations

import shutil

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import (
    append_file_path_to_file,
    read_file_paths_file,
    write_file_paths_file,
    write_text_file_contents,
)
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.journal import log_journal
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_state_directory_path,
)

# Permanent state that must NEVER be deleted
PERMANENT_DIRS = frozenset({"batches", "batch-sources"})

# Iteration-specific state (cleared by again, stop, abort)
ITERATION_STATE_FILES = [
    "selected-hunk-hash",
    "selected-hunk-patch",
    "selected-lines.json",
    "selected-binary-file.json",
    "index-snapshot",
    "working-tree-snapshot",
    "blocklist",
    "discarded-hunks",
    "included-hunks",
    "skipped-hunks.jsonl",
    "batched-hunks",
    "blocked-files",
    "processed.skip",
    "processed.include",
    "processed.batch",
]

# Session-level state (cleared by stop and abort, preserved by again)
SESSION_STATE_FILES = [
    "abort-head",
    "abort-stash",
    "snapshot-list",
    "snapshots",  # directory
    "auto-added-files",
    "intent-to-add-files",
    "iteration-count",
    "start-head",
    "start-index-tree",
    "start-batch-refs.json",
    "context-lines",
    "suggest-fixup-state.json",
    "session-batch-sources.json",
    "batch-refs-snapshot.json",
    "journal.jsonl",
]


def _snapshot_intent_to_add_files() -> tuple[list[str], list[str]]:
    """Snapshot all intent-to-add files so they survive git reset --hard on abort.

    Intent-to-add files (added with git add -N) are in the index with an empty blob
    but won't be captured by git stash. Since git reset --hard will wipe them out,
    we need to snapshot them upfront and record them so we can restore their status.

    Returns:
        Tuple of (all_intent_to_add_files, new_intent_to_add_files)
        - all_intent_to_add_files: All files with intent-to-add
        - new_intent_to_add_files: Only files absent from HEAD
    """
    from ..utils.paths import get_state_directory_path

    # Find all intent-to-add files (files in index with empty blob)
    EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    ls_result = run_git_command(["ls-files", "--stage"], check=True)

    all_intent_to_add_files = []
    new_intent_to_add_files = []

    for line in ls_result.stdout.strip().split('\n'):
        if not line:
            continue
        # Format: <mode> <hash> <stage>\t<path>
        parts = line.split()
        if len(parts) >= 4:
            blob_hash = parts[1]
            file_path = parts[3]
            if blob_hash == EMPTY_BLOB_HASH:
                # This is an intent-to-add file - snapshot it
                snapshot_file_if_untracked(file_path)
                all_intent_to_add_files.append(file_path)

                # Check if file exists in HEAD
                # Only new files absent from HEAD are safe to git rm --cached.
                head_check = run_git_command(["cat-file", "-e", f"HEAD:{file_path}"], check=False)
                if head_check.returncode != 0:
                    # File is absent from HEAD, so it is safe to remove from the index.
                    new_intent_to_add_files.append(file_path)

    # Save list of intent-to-add files for abort restoration
    if all_intent_to_add_files:
        intent_to_add_file = get_state_directory_path() / "intent-to-add-files"
        write_file_paths_file(intent_to_add_file, all_intent_to_add_files)

    return (all_intent_to_add_files, new_intent_to_add_files)


def initialize_abort_state() -> None:
    """Save selected HEAD and stash for abort functionality."""
    # Save selected HEAD
    head_result = run_git_command(["rev-parse", "HEAD"])
    write_text_file_contents(get_abort_head_file_path(), head_result.stdout.strip())

    # Snapshot all intent-to-add files upfront
    # These files won't survive git reset --hard but aren't in the stash
    # We need to snapshot them now so they can be restored on abort
    all_intent_to_add_files, new_intent_to_add_files = _snapshot_intent_to_add_files()

    # Temporarily remove new intent-to-add files from index so git stash create can succeed.
    # Intent-to-add files with content in working tree cause "not uptodate" errors
    # Only remove files absent from HEAD. Removing tracked files stages deletions.
    if new_intent_to_add_files:
        log_journal("session_removing_intent_to_add_files_for_stash", files=new_intent_to_add_files)
        run_git_command(["rm", "--cached", "--quiet", "--"] + new_intent_to_add_files, check=False)

    # Create stash of tracked file changes
    # git stash create (without -u) only captures changes to tracked files
    log_journal("session_creating_stash")
    stash_result = run_git_command(["stash", "create"], check=False)
    if stash_result.returncode == 0 and stash_result.stdout.strip():
        write_text_file_contents(get_abort_stash_file_path(), stash_result.stdout.strip())
        log_journal("session_stash_created", stash_hash=stash_result.stdout.strip())
    else:
        log_journal("session_stash_failed", returncode=stash_result.returncode, stderr=stash_result.stderr)

    # Re-add NEW intent-to-add files to index (the ones we removed)
    if new_intent_to_add_files:
        log_journal("session_re_adding_intent_to_add_files", files=new_intent_to_add_files)
        for file_path in new_intent_to_add_files:
            ls_before = run_git_command(["ls-files", "--stage", "--", file_path], check=False).stdout.strip()
            run_git_command(["add", "-N", "--", file_path], check=False)
            ls_after = run_git_command(["ls-files", "--stage", "--", file_path], check=False).stdout.strip()
            log_journal("session_re_add_intent_to_add", file_path=file_path, index_before=ls_before, index_after=ls_after)


def require_session_started() -> None:
    """Validate that a batch staging session is in progress.

    Raises:
        CommandError: If no session is active
    """
    if not get_abort_head_file_path().exists():
        raise CommandError(_("No session in progress. Run 'git-stage-batch start' first."))


def snapshot_file_if_untracked(file_path: str) -> None:
    """Snapshot an untracked file before modification for abort functionality.

    Args:
        file_path: Repository-relative path to the file
    """
    # Check index status using git ls-files --stage
    # - Not in output: untracked (should snapshot)
    # - Empty blob hash (e69de29...): intent-to-add (should snapshot)
    # - Real blob hash: tracked with content (don't snapshot)
    EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

    stage_result = run_git_command(["ls-files", "--stage", "--", file_path], check=False)
    if not stage_result.stdout.strip():
        # File not in index at all - it's untracked
        pass  # Continue to snapshot
    else:
        # File is in index - check if it has real content or is intent-to-add
        # Format: <mode> <hash> <stage>\t<path>
        parts = stage_result.stdout.strip().split()
        if len(parts) >= 2:
            blob_hash = parts[1]
            if blob_hash != EMPTY_BLOB_HASH:
                return  # File has real content in index, don't snapshot

    # Check if already snapshotted
    snapshotted_files = read_file_paths_file(get_abort_snapshot_list_file_path())
    if file_path in snapshotted_files:
        return  # Already snapshotted

    # Read selected file content
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    if not full_path.exists():
        return  # File doesn't exist

    # Save snapshot (use binary copy to handle all file types)
    snapshot_dir = get_abort_snapshots_directory_path()
    snapshot_path = snapshot_dir / file_path
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(full_path, snapshot_path)

    # Record snapshot in list
    append_file_path_to_file(get_abort_snapshot_list_file_path(), file_path)


def clear_iteration_state() -> None:
    """Clear iteration-specific state while preserving batches and session state.

    Deletes iteration-specific files (selected hunk, blocklist, snapshots, etc.)
    while preserving:
    - Batch metadata (batches/, batch-sources/)
    - Session state (abort state, context-lines, etc.)
    - Journal

    Called by: again command
    """
    state_dir = get_state_directory_path()

    for filename in ITERATION_STATE_FILES:
        file_path = state_dir / filename
        if file_path.exists():
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()


def clear_session_state() -> None:
    """Clear all session and iteration state while preserving permanent data.

    Deletes both session-level state and iteration state, while preserving:
    - Batch metadata (batches/, batch-sources/)

    Called by: stop and abort commands
    """
    state_dir = get_state_directory_path()

    # Clear session state files
    for filename in SESSION_STATE_FILES:
        file_path = state_dir / filename
        if file_path.exists():
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()

    # Clear iteration state
    clear_iteration_state()

    # Clean up state directory if it's now empty (only permanent dirs remain)
    try:
        if state_dir.exists():
            remaining = set(item.name for item in state_dir.iterdir())
            if not remaining:
                # Completely empty, remove the directory itself
                state_dir.rmdir()
            # else: permanent dirs remain, leave them
    except OSError:
        # Directory doesn't exist or can't be removed, that's fine
        pass
