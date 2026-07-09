"""Undo checkpoint orchestration."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .undo_refs import (
    SESSION_REDO_STACK_REF,
    SESSION_UNDO_STACK_REF,
    checkpoint_parent,
    clear_redo_history,
    current_redo_commit,
    current_undo_commit,
    list_restorable_refs,
)
from . import undo_restore as _undo_restore
from . import undo_worktree as _undo_worktree
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git import (
    GitIndexEntryUpdate,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    run_git_command,
    temp_git_index,
    update_git_refs,
)
from ..utils.git_object_io import create_git_blob
from ..utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
    get_state_directory_path,
)


_PENDING_CHECKPOINT: str | None = None


def _snapshot_current_state(paths: list[str]) -> dict[str, Any]:
    index_result = run_git_command(["write-tree"], check=False, requires_index_lock=False)
    return {
        "index_tree": index_result.stdout.strip() if index_result.returncode == 0 else None,
        "refs": list_restorable_refs(),
        "worktree_paths": _undo_worktree.snapshot_worktree_paths(paths),
    }


def _add_blob_to_index(env: dict[str, str], path: str, data: bytes, mode: str = "100644") -> None:
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


def _add_directory_to_index(env: dict[str, str], *, source_dir: Path, tree_prefix: str) -> None:
    if not source_dir.exists():
        return
    updates: list[GitIndexEntryUpdate] = []
    for file_path in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        relative_path = file_path.relative_to(source_dir).as_posix()
        tree_path = f"{tree_prefix}/{relative_path}"
        updates.append(
            _undo_worktree.index_update_from_path(
                index_path=tree_path,
                source_path=file_path,
                mode=_undo_worktree.file_mode_for_path(file_path),
            )
        )
    git_update_index_entries(updates, env=env)


def _create_undo_checkpoint(operation: str, *, worktree_paths: list[str] | None = None) -> str | None:
    """Create a before-image checkpoint for an undoable operation."""
    session_dir = get_state_directory_path() / "session"
    if not session_dir.exists():
        return None

    clear_redo_history()

    global _PENDING_CHECKPOINT

    tracked_worktree_paths = sorted(
        set(_undo_worktree.changed_worktree_paths()) | set(worktree_paths or [])
    )
    before = _snapshot_current_state(tracked_worktree_paths)

    manifest = {
        "operation": operation,
        "head": run_git_command(["rev-parse", "HEAD"], check=False, requires_index_lock=False).stdout.strip(),
        "index_tree": before["index_tree"],
        "refs": before["refs"],
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in before["worktree_paths"]
        ],
        "tracked_worktree_paths": tracked_worktree_paths,
    }

    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(env, source_dir=session_dir, tree_prefix="session")
        _add_directory_to_index(env, source_dir=get_batches_directory_path(), tree_prefix="batches")

        git_update_index_entries(
            [
                GitIndexEntryUpdate(
                    file_path=f"worktree/{entry['path']}",
                    mode=entry["mode"],
                    blob_sha=entry["blob"],
                )
                for entry in before["worktree_paths"]
                if entry["exists"] and entry.get("blob")
            ],
            env=env,
        )

        tree_sha = git_write_tree(env=env)

    parent = current_undo_commit()
    checkpoint_commit = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=f"Undo checkpoint: {operation}",
    )
    update_git_refs(updates=[(SESSION_UNDO_STACK_REF, checkpoint_commit)])
    _PENDING_CHECKPOINT = checkpoint_commit
    return checkpoint_commit


@contextmanager
def undo_checkpoint(operation: str, *, worktree_paths: list[str] | None = None) -> Iterator[None]:
    """Bracket an undoable operation with before and after snapshots."""
    if _PENDING_CHECKPOINT is not None:
        yield
        return

    checkpoint = _create_undo_checkpoint(operation, worktree_paths=worktree_paths)
    try:
        yield
    finally:
        if checkpoint is not None:
            finalize_pending_checkpoint()


def finalize_pending_checkpoint() -> None:
    """Record the post-operation state for conflict detection."""
    global _PENDING_CHECKPOINT
    checkpoint = _PENDING_CHECKPOINT
    if checkpoint is None:
        return
    _PENDING_CHECKPOINT = None

    current = current_undo_commit()
    if current != checkpoint:
        return

    try:
        manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    except CommandError:
        return

    paths = sorted(
        set(manifest.get("tracked_worktree_paths", []))
        | set(_undo_worktree.changed_worktree_paths())
    )
    manifest["after"] = _snapshot_current_state(paths)
    manifest["after"]["worktree_paths"] = manifest["after"]["worktree_paths"]

    with temp_git_index() as env:
        git_read_tree(checkpoint, env=env)
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        tree_sha = git_write_tree(env=env)

    parent = checkpoint_parent(checkpoint)
    checkpoint_commit = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=f"Undo checkpoint: {manifest.get('operation', 'operation')}",
    )
    update_git_refs(updates=[(SESSION_UNDO_STACK_REF, checkpoint_commit)])


def _worktree_state_by_path(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in entries}


def _detect_conflicts_against_state(expected_state: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    current = _snapshot_current_state([entry["path"] for entry in expected_state.get("worktree_paths", [])])

    if current.get("index_tree") != expected_state.get("index_tree"):
        conflicts.append(_("index"))

    if current.get("refs") != expected_state.get("refs"):
        conflicts.append(_("batch refs"))

    expected_worktree = _worktree_state_by_path(expected_state.get("worktree_paths", []))
    current_worktree = _worktree_state_by_path(current.get("worktree_paths", []))
    for path, expected in sorted(expected_worktree.items()):
        actual = current_worktree.get(path)
        if actual != expected:
            conflicts.append(path)

    return conflicts


def _detect_conflicts(manifest: dict[str, Any]) -> list[str]:
    after = manifest.get("after")
    if not isinstance(after, dict):
        return []
    return _detect_conflicts_against_state(after)


def _detect_redo_conflicts(manifest: dict[str, Any]) -> list[str]:
    after_undo = manifest.get("after_undo")
    if not isinstance(after_undo, dict):
        return []
    return _detect_conflicts_against_state(after_undo)


def _redo_relevant_paths(manifest: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    paths.update(manifest.get("tracked_worktree_paths", []))
    for entry in manifest.get("worktree_paths", []):
        paths.add(entry["path"])
    after = manifest.get("after")
    if isinstance(after, dict):
        for entry in after.get("worktree_paths", []):
            paths.add(entry["path"])
    paths.update(_undo_worktree.changed_worktree_paths())
    return sorted(paths)


def _write_snapshot_commit(
    *,
    ref_name: str,
    message: str,
    manifest: dict[str, Any],
    session_dir: Path,
    batches_dir: Path,
    worktree_entries: list[dict[str, Any]],
    parent: str | None,
) -> str:
    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(env, source_dir=session_dir, tree_prefix="session")
        _add_directory_to_index(env, source_dir=batches_dir, tree_prefix="batches")

        repo_root = get_git_repository_root_path()
        index_updates: list[GitIndexEntryUpdate] = []
        for entry in worktree_entries:
            if entry.get("kind") in {"gitlink", "embedded-repo"}:
                continue
            if not entry.get("exists", False):
                continue
            blob_sha = entry.get("blob")
            if blob_sha:
                index_updates.append(
                    GitIndexEntryUpdate(
                        file_path=f"worktree/{entry['path']}",
                        mode=entry.get("mode", "100644"),
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
                            blob_sha=_undo_worktree.create_blob_from_worktree_path(
                                full_path,
                                mode=mode,
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


def _push_redo_node(
    *,
    operation: str,
    undo_checkpoint: str,
    target: dict[str, Any],
    target_session_dir: Path,
    target_batches_dir: Path,
    after_undo: dict[str, Any],
    worktree_entries: list[dict[str, Any]],
) -> str:
    manifest = {
        "operation": operation,
        "undo_checkpoint": undo_checkpoint,
        "head": target.get(
            "head",
            run_git_command(["rev-parse", "HEAD"], check=False, requires_index_lock=False).stdout.strip(),
        ),
        "index_tree": target.get("index_tree"),
        "refs": target.get("refs", {}),
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in worktree_entries
        ],
        "after_undo": after_undo,
    }

    parent = current_redo_commit()
    return _write_snapshot_commit(
        ref_name=SESSION_REDO_STACK_REF,
        message=f"Redo node: {operation}",
        manifest=manifest,
        session_dir=target_session_dir,
        batches_dir=target_batches_dir,
        worktree_entries=worktree_entries,
        parent=parent,
    )


def undo_last_checkpoint(*, force: bool = False) -> str:
    """Restore the latest undo checkpoint and pop it from the undo stack."""
    finalize_pending_checkpoint()
    checkpoint = current_undo_commit()
    if checkpoint is None:
        raise CommandError(_("Nothing to undo."))

    manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    conflicts = _detect_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot undo because current state has changed since the checkpoint: {items}.\n"
              "Run 'git-stage-batch undo --force' to overwrite those changes.").format(items=preview)
        )

    operation = str(manifest.get("operation", "operation"))
    redo_paths = _redo_relevant_paths(manifest)
    redo_target = _snapshot_current_state(redo_paths)
    redo_worktree_entries = _undo_worktree.snapshot_worktree_paths(redo_paths)

    redo_session_dir = tempfile.mkdtemp(prefix="gsb-redo-session-")
    redo_batches_dir = tempfile.mkdtemp(prefix="gsb-redo-batches-")
    try:
        live_session_dir = get_session_directory_path()
        live_batches_dir = get_batches_directory_path()
        if live_session_dir.exists():
            shutil.copytree(live_session_dir, redo_session_dir, dirs_exist_ok=True)
        if live_batches_dir.exists():
            shutil.copytree(live_batches_dir, redo_batches_dir, dirs_exist_ok=True)

        _undo_restore.restore_tree_prefix(
            checkpoint,
            prefix="session",
            target_dir=live_session_dir,
        )
        _undo_restore.restore_tree_prefix(
            checkpoint,
            prefix="batches",
            target_dir=live_batches_dir,
        )
        _undo_restore.restore_refs(manifest.get("refs", {}))

        index_tree = manifest.get("index_tree")
        if index_tree:
            git_read_tree(index_tree)

        _undo_restore.restore_worktree(checkpoint, manifest)
        _undo_restore.restore_intent_to_add_entries()

        after_undo = _snapshot_current_state(redo_paths)

        _push_redo_node(
            operation=operation,
            undo_checkpoint=checkpoint,
            target=redo_target,
            target_session_dir=Path(redo_session_dir),
            target_batches_dir=Path(redo_batches_dir),
            after_undo=after_undo,
            worktree_entries=redo_worktree_entries,
        )
    finally:
        shutil.rmtree(redo_session_dir, ignore_errors=True)
        shutil.rmtree(redo_batches_dir, ignore_errors=True)

    parent = checkpoint_parent(checkpoint)
    if parent:
        update_git_refs(updates=[(SESSION_UNDO_STACK_REF, parent)])
    else:
        update_git_refs(deletes=[SESSION_UNDO_STACK_REF])

    return operation


def redo_last_checkpoint(*, force: bool = False) -> str:
    """Reapply the most recently undone operation from the redo stack."""
    finalize_pending_checkpoint()
    redo_node = current_redo_commit()
    if redo_node is None:
        raise CommandError(_("Nothing to redo."))

    manifest = _undo_restore.read_json_from_commit(redo_node, "manifest.json")
    conflicts = _detect_redo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot redo because current state has changed since the undo: {items}.\n"
              "Run 'git-stage-batch redo --force' to overwrite those changes.").format(items=preview)
        )

    _undo_restore.restore_tree_prefix(
        redo_node,
        prefix="session",
        target_dir=get_session_directory_path(),
    )
    _undo_restore.restore_tree_prefix(
        redo_node,
        prefix="batches",
        target_dir=get_batches_directory_path(),
    )
    _undo_restore.restore_refs(manifest.get("refs", {}))

    index_tree = manifest.get("index_tree")
    if index_tree:
        git_read_tree(index_tree)

    _undo_restore.restore_worktree(redo_node, manifest)
    _undo_restore.restore_intent_to_add_entries()

    undo_checkpoint = manifest.get("undo_checkpoint")
    if undo_checkpoint:
        update_git_refs(updates=[(SESSION_UNDO_STACK_REF, undo_checkpoint)])

    parent = checkpoint_parent(redo_node)
    if parent:
        update_git_refs(updates=[(SESSION_REDO_STACK_REF, parent)])
    else:
        update_git_refs(deletes=[SESSION_REDO_STACK_REF])

    return str(manifest.get("operation", "operation"))
