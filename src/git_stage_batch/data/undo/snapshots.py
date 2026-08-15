"""Capture and serialize undo checkpoint snapshots."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import worktree as _undo_worktree
from ..recovery_types import (
    CheckpointState,
    FilesystemEntryState,
    FilesystemState,
    WorktreePathState,
    worktree_metadata_without_blob,
)
from ..index_entries import (
    read_index_path_entries,
    read_intent_to_add_paths,
)
from ..recovery_anchors import (
    anchor_recovery_objects,
    state_recovery_objects,
)
from .refs import (
    SESSION_REDO_STACK_REF,
    current_redo_commit,
    list_restorable_refs,
)
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_object_io import create_git_blob, create_git_blobs_from_paths
from ...utils.git_refs import update_git_refs
from ...utils.git_repository import get_git_repository_root_path
from ...utils.session_start_point import current_head_commit
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _


def snapshot_current_state(
    worktree_paths: list[str],
    *,
    index_paths: list[str] | None = None,
    ref_names: list[str] | None = None,
) -> CheckpointState:
    """Capture the checkpoint-managed portion of current repository state."""
    if index_paths is None:
        index_paths = worktree_paths
    index_path_entries = read_index_path_entries(index_paths)
    unmerged_paths = sorted(
        file_path
        for file_path, entries in index_path_entries.items()
        if entries.is_unmerged
    )
    if unmerged_paths:
        raise CommandError(
            _(
                "Cannot create a recovery checkpoint while the index contains "
                "unmerged entries: {paths}"
            ).format(
                paths=", ".join(display_path(path) for path in unmerged_paths),
            )
        )
    index_entries = {
        file_path: entries.stage_zero
        for file_path, entries in index_path_entries.items()
        if entries.stage_zero is not None
    }
    refs = list_restorable_refs()
    if ref_names is not None:
        refs = {name: refs[name] for name in ref_names if name in refs}
    return {
        "index_entries": {
            path: {"mode": entry.mode, "object_id": entry.object_id}
            for path, entry in sorted(index_entries.items())
        },
        "refs": refs,
        "intent_to_add_paths": sorted(read_intent_to_add_paths(index_paths)),
        "worktree_paths": _undo_worktree.snapshot_worktree_paths(worktree_paths),
    }


def add_blob_to_index(
    env: dict[str, str],
    path: str,
    data: bytes,
    mode: str = "100644",
) -> None:
    """Add an in-memory checkpoint blob to a temporary index."""
    git_update_index_entries(
        [
            GitIndexEntryUpdate(
                file_path=path,
                mode=mode,
                blob_sha=create_git_blob([data]),
            )
        ],
        env=env,
    )


def add_directory_to_index(
    env: dict[str, str],
    *,
    source_dir: Path,
    tree_prefix: str,
    relative_paths: list[str] | None = None,
    filesystem_state: FilesystemState | None = None,
) -> None:
    """Add all selected application-state files to a temporary index."""
    state = (
        filesystem_directory_state(
            source_dir,
            relative_paths=relative_paths,
        )
        if filesystem_state is None
        else filesystem_state
    )
    updates = [
        GitIndexEntryUpdate(
            file_path=f"{tree_prefix}/{relative_path}",
            mode=entry["mode"],
            blob_sha=entry["object_id"],
        )
        for relative_path, entry in sorted(state.items())
    ]
    git_update_index_entries(updates, env=env)


def filesystem_directory_state(
    source_dir: Path,
    *,
    relative_paths: list[str] | None = None,
) -> FilesystemState:
    """Return content identities for application-state files."""
    if not source_dir.exists():
        return {}
    if relative_paths is None:
        file_paths = sorted(path for path in source_dir.rglob("*") if path.is_file())
    else:
        file_paths = sorted(
            source_dir / relative_path
            for relative_path in relative_paths
            if (source_dir / relative_path).is_file()
        )
    normal_file_blobs = create_git_blobs_from_paths(
        path for path in file_paths if not path.is_symlink()
    )
    state: FilesystemState = {}
    for file_path in file_paths:
        relative_path = file_path.relative_to(source_dir).as_posix()
        mode = _undo_worktree.file_mode_for_path(file_path)
        if file_path.is_symlink():
            object_id = _undo_worktree.create_blob_from_worktree_path(
                file_path,
                mode=mode,
            )
        else:
            object_id = normal_file_blobs[file_path]
        entry: FilesystemEntryState = {"mode": mode, "object_id": object_id}
        if mode != "120000":
            entry["permissions"] = _undo_worktree.file_permissions_for_path(file_path)
        state[relative_path] = entry
    return state


def write_snapshot_commit(
    *,
    ref_name: str,
    message: str,
    manifest: CheckpointState,
    session_dir: Path,
    batches_dir: Path,
    repository_dir: Path,
    worktree_entries: list[WorktreePathState],
    parent: str | None,
    session_paths: list[str] | None = None,
    batch_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
) -> str:
    """Write a complete checkpoint snapshot and advance its stack ref."""
    with temp_git_index() as env:
        add_blob_to_index(
            env,
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        add_directory_to_index(
            env,
            source_dir=session_dir,
            tree_prefix="session",
            relative_paths=session_paths,
            filesystem_state=manifest.get("session_files"),
        )
        add_directory_to_index(
            env,
            source_dir=batches_dir,
            tree_prefix="batches",
            relative_paths=batch_paths,
            filesystem_state=manifest.get("batches_files"),
        )
        add_directory_to_index(
            env,
            source_dir=repository_dir,
            tree_prefix="repository",
            relative_paths=repository_paths,
            filesystem_state=manifest.get("repository_files"),
        )

        repo_root = get_git_repository_root_path()
        index_updates: list[GitIndexEntryUpdate] = []
        for entry in worktree_entries:
            if entry.get("kind") in {"gitlink", "embedded-repo"} and not entry.get(
                "archive"
            ):
                continue
            if not entry.get("exists", False):
                continue
            blob_sha = entry.get("blob")
            if blob_sha:
                index_updates.append(
                    GitIndexEntryUpdate(
                        file_path=f"worktree/{entry['path']}",
                        mode=entry.get(
                            "storage_mode",
                            entry.get("mode", "100644"),
                        ),
                        blob_sha=blob_sha,
                    )
                )
            else:
                full_path = repo_root / entry["path"]
                if os.path.lexists(full_path):
                    mode = _undo_worktree.file_mode_for_path(full_path)
                    index_updates.append(
                        GitIndexEntryUpdate(
                            file_path=f"worktree/{entry['path']}",
                            mode=mode,
                            blob_sha=(
                                _undo_worktree.create_blob_from_worktree_path(
                                    full_path,
                                    mode=mode,
                                )
                            ),
                        )
                    )
        git_update_index_entries(index_updates, env=env)
        tree_sha = git_write_tree(env=env)

    commit_sha = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=message,
    )
    update_git_refs(updates=[(ref_name, commit_sha)])
    return commit_sha


def push_redo_node(
    *,
    operation: str,
    undo_checkpoint: str,
    target: CheckpointState,
    target_session_dir: Path,
    target_batches_dir: Path,
    target_repository_dir: Path,
    after_undo: CheckpointState,
    worktree_entries: list[WorktreePathState],
    session_paths: list[str],
    batch_paths: list[str],
    repository_paths: list[str],
) -> str:
    """Serialize the after-image needed to redo one undone operation."""
    recovery_objects = state_recovery_objects(target)
    recovery_objects.update(state_recovery_objects(after_undo))
    recovery_objects.add(undo_checkpoint)
    manifest: CheckpointState = {
        "operation": operation,
        "undo_checkpoint": undo_checkpoint,
        "head": target.get(
            "head",
            current_head_commit(),
        ),
        "index_entries": target.get("index_entries", {}),
        "intent_to_add_paths": target.get("intent_to_add_paths", []),
        "tracked_index_paths": target.get("tracked_index_paths", []),
        "refs": target.get("refs", {}),
        "tracked_refs": target.get("tracked_refs", []),
        "tracked_session_paths": session_paths,
        "tracked_batches_paths": batch_paths,
        "tracked_repository_paths": repository_paths,
        "session_files": target.get("session_files", {}),
        "batches_files": target.get("batches_files", {}),
        "repository_files": target.get("repository_files", {}),
        "worktree_paths": [
            worktree_metadata_without_blob(entry)
            for entry in worktree_entries
        ],
        "after_undo": after_undo,
        "recovery_anchors": anchor_recovery_objects(recovery_objects),
    }

    return write_snapshot_commit(
        ref_name=SESSION_REDO_STACK_REF,
        message=f"Redo node: {operation}",
        manifest=manifest,
        session_dir=target_session_dir,
        batches_dir=target_batches_dir,
        repository_dir=target_repository_dir,
        worktree_entries=worktree_entries,
        parent=current_redo_commit(),
        session_paths=session_paths,
        batch_paths=batch_paths,
        repository_paths=repository_paths,
    )


def copy_tracked_repository_files(
    source_dir: Path,
    target_dir: Path,
    relative_paths: list[str],
) -> None:
    """Copy scoped Git-admin files for a redo before-image."""
    for relative_path in relative_paths:
        source_path = source_dir / relative_path
        if not os.path.lexists(source_path):
            continue
        target_path = target_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path, follow_symlinks=False)
