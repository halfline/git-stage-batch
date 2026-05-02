"""Abort command implementation."""

from __future__ import annotations

import os
import shutil
import sys

from ..data.batch_refs import restore_batch_refs
from ..data.session import clear_session_state
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_io import read_file_paths_file, read_text_file_contents
from ..utils.git import get_git_repository_root_path, require_git_repository, run_git_command
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_abort_state_directory_path,
)


def command_abort() -> None:
    """Abort the session and undo all changes including commits and discards."""
    require_git_repository()

    # Check if abort state exists
    if not get_abort_head_file_path().exists():
        exit_with_error(_("No session to abort. Abort state not found."))

    # Read abort state
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    abort_stash_path = get_abort_stash_file_path()
    abort_stash = read_text_file_contents(abort_stash_path).strip() if abort_stash_path.exists() else None

    # Reset auto-added files first
    if get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    # Reset to start HEAD (undoes commits, resets index and tracked files)
    # Set GIT_REFLOG_ACTION for clear reflog entries
    env = os.environ.copy()
    env["GIT_REFLOG_ACTION"] = "stage-batch abort"

    print(_("Resetting to {}...").format(abort_head[:7]), file=sys.stderr)
    run_git_command(
        ["reset", "--hard", abort_head],
        env=env,
    )

    # Apply original stash if it exists (with --index to restore staged state)
    if abort_stash:
        print(_("Applying original changes..."), file=sys.stderr)
        result = run_git_command(
            ["stash", "apply", "--index", abort_stash],
            env=env,
            check=False,
        )
        if result.returncode != 0:
            print(_("⚠ Warning: Could not apply stash cleanly: {}").format(result.stderr), file=sys.stderr)

    # Restore snapshotted untracked files
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        repo_root = get_git_repository_root_path()
        snapshots_dir = get_abort_snapshots_directory_path()

        for file_path in snapshotted_files:
            snapshot_path = snapshots_dir / file_path
            if snapshot_path.exists():
                target_path = repo_root / file_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot_path, target_path)
                print(_("Restored: {}").format(file_path), file=sys.stderr)

    # Restore intent-to-add status for files that had it before session
    intent_to_add_path = get_abort_state_directory_path() / "intent-to-add-files.txt"
    if intent_to_add_path.exists():
        intent_to_add_files = read_file_paths_file(intent_to_add_path)
        for file_path in intent_to_add_files:
            # Re-add with intent-to-add flag
            run_git_command(["add", "-N", file_path], check=False)

    # Restore batch refs to their original state
    # This recreates both git refs and metadata files from the snapshot
    restore_batch_refs()

    # Clear all session state (preserves batches and batch-sources)
    # Do this AFTER restore_batch_refs so snapshot file is available
    clear_session_state()

    print(_("✓ Session aborted. All changes reverted."), file=sys.stderr)
