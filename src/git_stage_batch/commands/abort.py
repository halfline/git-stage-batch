"""Abort command implementation."""

from __future__ import annotations

import os
import shutil
import sys
import json

from ..data.batch_refs import restore_batch_refs
from ..data.session import clear_session_state
from ..data.session_ownership import (
    release_session_ownership,
    require_current_session_owner,
    require_no_foreign_session_owner,
)
from ..data.recovery_anchors import validate_recovery_objects
from ..utils.session_start_point import load_session_start_point
from ..data.start_time_changes import read_staged_renames
from ..exceptions import CommandError, exit_with_error
from ..i18n import _
from ..utils.file_io import read_file_paths_file, read_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.git_worktree import (
    git_apply_stash,
    git_reset_hard,
)
from ..utils.git_index import (
    git_add_paths,
    git_read_tree,
    git_reset_paths,
)
from ..utils.git_refs import update_git_refs
from ..utils.git_repository import (
    get_git_repository_root_path,
    require_git_repository,
)
from ..utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_abort_state_directory_path,
    get_abort_recovery_anchors_file_path,
)


def _remove_normalized_rename_destinations_before_stash_apply() -> None:
    renames = read_staged_renames()
    if not renames:
        return

    repo_root = get_git_repository_root_path()
    for rename in renames:
        tracked_result = run_git_command(
            ["ls-files", "--error-unmatch", "--", rename.new_path],
            check=False,
            requires_index_lock=False,
        )
        if tracked_result.returncode == 0:
            continue

        target_path = repo_root / rename.new_path
        if target_path.is_dir() and not target_path.is_symlink():
            shutil.rmtree(target_path)
        else:
            target_path.unlink(missing_ok=True)


def command_abort(*, quiet: bool = False) -> None:
    """Abort the session and undo all changes including commits and discards."""
    require_git_repository()
    require_no_foreign_session_owner()

    # Check if abort state exists
    if not get_abort_head_file_path().exists():
        exit_with_error(_("No session to abort. Abort state not found."))
    require_current_session_owner()

    # Read abort state
    abort_head = read_text_file_contents(get_abort_head_file_path()).strip()
    start_point = load_session_start_point()
    abort_stash_path = get_abort_stash_file_path()
    abort_stash = (
        read_text_file_contents(abort_stash_path).strip()
        if abort_stash_path.exists()
        else None
    )
    recovery_objects = [start_point.head_commit, start_point.index_tree, abort_stash]
    batch_snapshot_path = get_abort_state_directory_path() / "batch-refs.json"
    try:
        batch_snapshot = json.loads(read_text_file_contents(batch_snapshot_path))
    except json.JSONDecodeError:
        batch_snapshot = {}
    for batch_state in batch_snapshot.values():
        recovery_objects.extend(
            [batch_state.get("commit_sha"), batch_state.get("state_commit_sha")]
        )
    try:
        recovery_anchors = json.loads(
            read_text_file_contents(get_abort_recovery_anchors_file_path())
        )
    except json.JSONDecodeError:
        recovery_anchors = None
    validate_recovery_objects(recovery_objects, anchors=recovery_anchors)

    # Reset auto-added files first
    if not start_point.is_unborn and get_auto_added_files_file_path().exists():
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        if auto_added:
            git_reset_paths(auto_added)

    # Reset to start HEAD (undoes commits, resets index and tracked files)
    # Set GIT_REFLOG_ACTION for clear reflog entries
    env = os.environ.copy()
    env["GIT_REFLOG_ACTION"] = "stage-batch abort"

    if start_point.is_unborn:
        if start_point.symbolic_head:
            update_git_refs(deletes=[start_point.symbolic_head])
        git_read_tree(start_point.index_tree)
    else:
        if not quiet:
            print(_("Resetting to {}...").format(abort_head[:7]), file=sys.stderr)
        git_reset_hard(abort_head, env=env)
        _remove_normalized_rename_destinations_before_stash_apply()

    # Apply original stash if it exists (with --index to restore staged state)
    if abort_stash:
        if not quiet:
            print(_("Applying original changes..."), file=sys.stderr)
        result = git_apply_stash(abort_stash, restore_index=True, env=env, check=False)
        if result.returncode != 0:
            raise CommandError(
                _(
                    "Could not restore the session's original changes: {error}\n"
                    "The session remains active. Resolve the obstruction and run "
                    "'git-stage-batch abort' again."
                ).format(error=result.stderr.strip())
            )

    # Restore snapshotted untracked files
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        repo_root = get_git_repository_root_path()
        snapshots_dir = get_abort_snapshots_directory_path()

        # Refuse every obstructed directory before restoring any snapshot so a
        # later conflict cannot make an earlier directory block the retry.
        for file_path in snapshotted_files:
            snapshot_path = snapshots_dir / file_path
            if not snapshot_path.is_dir() or snapshot_path.is_symlink():
                continue
            target_path = repo_root / file_path
            if os.path.lexists(target_path):
                raise CommandError(
                    _(
                        "Could not restore untracked directory {file}: "
                        "the path already exists. The session remains active."
                    ).format(file=file_path)
                )

        for file_path in snapshotted_files:
            snapshot_path = snapshots_dir / file_path
            if os.path.lexists(snapshot_path):
                target_path = repo_root / file_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                if snapshot_path.is_dir() and not snapshot_path.is_symlink():
                    shutil.copytree(snapshot_path, target_path, symlinks=True)
                elif snapshot_path.is_symlink():
                    if target_path.is_dir() and not target_path.is_symlink():
                        raise CommandError(
                            _(
                                "Could not restore untracked symlink {file}: "
                                "the path is now a directory. The session remains active."
                            ).format(file=file_path)
                        )
                    if os.path.lexists(target_path):
                        target_path.unlink()
                    link_target = os.readlink(os.fsencode(snapshot_path))
                    os.symlink(link_target, os.fsencode(target_path))
                else:
                    if target_path.is_symlink():
                        target_path.unlink()
                    shutil.copy2(snapshot_path, target_path)
                if not quiet:
                    print(_("Restored: {}").format(file_path), file=sys.stderr)

    # Restore intent-to-add status for files that had it before session
    intent_to_add_path = get_abort_state_directory_path() / "intent-to-add-files.txt"
    if intent_to_add_path.exists():
        intent_to_add_files = read_file_paths_file(intent_to_add_path)
        for file_path in intent_to_add_files:
            # Re-add with intent-to-add flag
            git_add_paths([file_path], intent_to_add=True)

    # Restore batch refs to their original state
    # This recreates both git refs and metadata files from the snapshot
    restore_batch_refs()

    # Clear all session state (preserves batches and batch-sources)
    # Do this AFTER restore_batch_refs so snapshot file is available
    clear_session_state()
    release_session_ownership()

    if not quiet:
        print(_("✓ Session aborted. All changes reverted."), file=sys.stderr)
