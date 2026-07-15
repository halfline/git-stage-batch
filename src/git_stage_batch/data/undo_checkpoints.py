"""Undo checkpoint stack orchestration."""

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
    current_redo_commit,
    current_undo_commit,
    list_restorable_refs,
)
from .recovery_anchors import (
    anchor_recovery_objects,
    anchor_recovery_state,
    state_recovery_objects,
    validate_recovery_state,
)
from ..utils.session_start_point import current_head_commit
from . import undo_restore as _undo_restore
from . import undo_worktree as _undo_worktree
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import (
    run_git_command,
)
from ..utils.git_refs import (
    update_git_refs,
)
from ..utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from .index_entries import read_index_entries
from .session import intent_to_add_files
from ..utils.git_repository import get_git_directory_path, get_git_repository_root_path
from ..utils.git_object_io import create_git_blob, create_git_blobs_from_paths
from ..utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
    get_state_directory_path,
)


_PENDING_CHECKPOINT: str | None = None
_PENDING_CHECKPOINT_REPOSITORY: Path | None = None
_PENDING_CHECKPOINT_ROLLBACK_ON_ERROR: bool | None = None
_PENDING_CHECKPOINT_ROLLBACK_CAUSE: BaseException | None = None
EXPLICIT_WORKTREE_SCOPE = "explicit"
CHANGED_WORKTREE_SCOPE = "changed"


def _clear_pending_checkpoint() -> None:
    """Forget process-local state for the pending checkpoint."""
    global _PENDING_CHECKPOINT, _PENDING_CHECKPOINT_REPOSITORY
    global _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR, _PENDING_CHECKPOINT_ROLLBACK_CAUSE
    _PENDING_CHECKPOINT = None
    _PENDING_CHECKPOINT_REPOSITORY = None
    _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR = None
    _PENDING_CHECKPOINT_ROLLBACK_CAUSE = None


def _validate_nested_checkpoint(
    checkpoint: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None,
    repository_paths: list[str] | None,
    rollback_on_error: bool,
) -> None:
    """Require a nested operation to fit inside the active transaction."""
    manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    requested_index_paths = worktree_paths if index_paths is None else index_paths
    scope_pairs = (
        (
            _("worktree"),
            set(worktree_paths),
            set(manifest.get("tracked_worktree_paths", [])),
        ),
        (
            _("index"),
            set(requested_index_paths),
            set(
                manifest.get(
                    "tracked_index_paths",
                    manifest.get("tracked_worktree_paths", []),
                )
            ),
        ),
        (
            _("repository"),
            set(repository_paths or []),
            set(manifest.get("tracked_repository_paths", [])),
        ),
    )
    for scope_name, requested_paths, tracked_paths in scope_pairs:
        missing_paths = sorted(requested_paths - tracked_paths)
        if missing_paths:
            raise CommandError(
                _(
                    "Cannot start nested undoable operation because the outer "
                    "checkpoint does not cover {scope} path(s): {paths}"
                ).format(
                    scope=scope_name,
                    paths=", ".join(missing_paths),
                )
            )

    if rollback_on_error and not _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR:
        raise CommandError(
            _(
                "Cannot start nested transactional operation because the outer "
                "checkpoint does not roll back on error."
            )
        )


def _checkpoint_worktree_scope(
    worktree_paths: list[str],
) -> tuple[str, list[str]]:
    """Return checkpoint scope metadata and paths to snapshot."""
    return EXPLICIT_WORKTREE_SCOPE, sorted(set(worktree_paths))


def _uses_explicit_worktree_scope(manifest: dict[str, Any]) -> bool:
    """Return whether a checkpoint intentionally scoped worktree snapshots."""
    return manifest.get("worktree_path_scope") == EXPLICIT_WORKTREE_SCOPE


def _snapshot_current_state(
    worktree_paths: list[str],
    *,
    index_paths: list[str] | None = None,
    ref_names: list[str] | None = None,
) -> dict[str, Any]:
    if index_paths is None:
        index_paths = worktree_paths
    index_entries = read_index_entries(index_paths)
    refs = list_restorable_refs()
    if ref_names is not None:
        refs = {name: refs[name] for name in ref_names if name in refs}
    return {
        "index_entries": {
            path: {"mode": entry.mode, "object_id": entry.object_id}
            for path, entry in sorted(index_entries.items())
        },
        "refs": refs,
        "intent_to_add_paths": intent_to_add_files(index_paths),
        "worktree_paths": _undo_worktree.snapshot_worktree_paths(worktree_paths),
    }


def _restore_intent_to_add_state(state: dict[str, Any]) -> None:
    """Restore exact intent-to-add flags and fail closed for legacy checkpoints."""
    saved_paths = state.get("intent_to_add_paths")
    if isinstance(saved_paths, list):
        paths = [path for path in saved_paths if isinstance(path, str)]
    else:
        # Legacy checkpoints did not save the intent-to-add bit. An empty
        # index blob is ambiguous: it can be either intent-to-add or a fully
        # staged empty file. Failing closed avoids demoting staged content
        # based on append-only session history.
        paths = []
    _undo_restore.restore_intent_to_add_entries(paths)


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


def _add_directory_to_index(
    env: dict[str, str],
    *,
    source_dir: Path,
    tree_prefix: str,
    relative_paths: list[str] | None = None,
) -> None:
    state = _filesystem_directory_state(
        source_dir,
        relative_paths=relative_paths,
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


def _filesystem_directory_state(
    source_dir: Path,
    *,
    relative_paths: list[str] | None = None,
) -> dict[str, dict[str, str]]:
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
    state: dict[str, dict[str, str]] = {}
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
        state[relative_path] = {"mode": mode, "object_id": object_id}
    return state


def _create_undo_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
) -> str | None:
    """Create a before-image checkpoint for an undoable operation."""
    session_dir = get_state_directory_path() / "session"
    if not session_dir.exists():
        return None

    global _PENDING_CHECKPOINT, _PENDING_CHECKPOINT_REPOSITORY

    worktree_path_scope, tracked_worktree_paths = _checkpoint_worktree_scope(
        worktree_paths
    )
    tracked_index_paths = sorted(
        set(worktree_paths if index_paths is None else index_paths)
    )
    tracked_repository_paths = sorted(set(repository_paths or []))
    before = _snapshot_current_state(
        tracked_worktree_paths,
        index_paths=tracked_index_paths,
    )
    recovery_anchors = anchor_recovery_state(before)

    manifest = {
        "operation": operation,
        "head": current_head_commit(),
        "index_entries": before["index_entries"],
        "intent_to_add_paths": before["intent_to_add_paths"],
        "refs": before["refs"],
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in before["worktree_paths"]
        ],
        "tracked_worktree_paths": tracked_worktree_paths,
        "tracked_index_paths": tracked_index_paths,
        "tracked_repository_paths": tracked_repository_paths,
        "worktree_path_scope": worktree_path_scope,
        "recovery_anchors": recovery_anchors,
    }

    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(env, source_dir=session_dir, tree_prefix="session")
        _add_directory_to_index(env, source_dir=get_batches_directory_path(), tree_prefix="batches")
        if tracked_repository_paths:
            _add_directory_to_index(
                env,
                source_dir=get_git_directory_path(),
                tree_prefix="repository",
                relative_paths=tracked_repository_paths,
            )

        git_update_index_entries(
            [
                GitIndexEntryUpdate(
                    file_path=f"worktree/{entry['path']}",
                    mode=entry.get("storage_mode", entry["mode"]),
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
    update_git_refs(
        updates=[(SESSION_UNDO_STACK_REF, checkpoint_commit)],
        deletes=[SESSION_REDO_STACK_REF],
    )
    _PENDING_CHECKPOINT = checkpoint_commit
    _PENDING_CHECKPOINT_REPOSITORY = get_git_directory_path()
    return checkpoint_commit


@contextmanager
def undo_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
    rollback_on_error: bool = False,
) -> Iterator[None]:
    """Bracket an undoable operation with before and after snapshots.

    When rollback_on_error is true, restore the before-image and discard the
    checkpoint before propagating an exception. This turns the checkpoint into
    a transaction boundary for commands that span several files or stores.
    """
    global _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR
    global _PENDING_CHECKPOINT_ROLLBACK_CAUSE

    if _PENDING_CHECKPOINT is not None:
        current_repository = get_git_directory_path()
        if (
            _PENDING_CHECKPOINT_REPOSITORY is not None
            and current_repository != _PENDING_CHECKPOINT_REPOSITORY
        ):
            _clear_pending_checkpoint()
        elif current_undo_commit() == _PENDING_CHECKPOINT:
            _validate_nested_checkpoint(
                _PENDING_CHECKPOINT,
                worktree_paths=worktree_paths,
                index_paths=index_paths,
                repository_paths=repository_paths,
                rollback_on_error=rollback_on_error,
            )
            try:
                yield
            except BaseException as nested_error:
                if rollback_on_error:
                    _PENDING_CHECKPOINT_ROLLBACK_CAUSE = nested_error
                raise
            return
        else:
            _clear_pending_checkpoint()
            raise CommandError(
                _(
                    "Cannot start an undoable operation because the pending "
                    "checkpoint reference moved."
                )
            )

    previous_redo = current_redo_commit()
    checkpoint = _create_undo_checkpoint(
        operation,
        worktree_paths=worktree_paths,
        index_paths=index_paths,
        repository_paths=repository_paths,
    )
    if checkpoint is not None:
        _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR = rollback_on_error
    try:
        yield
    except BaseException as operation_error:
        if checkpoint is not None and rollback_on_error:
            try:
                _rollback_failed_checkpoint(
                    checkpoint,
                    previous_redo=previous_redo,
                )
            except Exception as rollback_error:
                raise CommandError(
                    _(
                        "Operation failed and its automatic rollback also failed. "
                        "The before-image remains available through `undo --force`.\n"
                        "Operation error: {operation_error}\n"
                        "Rollback error: {rollback_error}"
                    ).format(
                        operation_error=operation_error,
                        rollback_error=rollback_error,
                    )
                ) from operation_error
        elif checkpoint is not None:
            finalize_pending_checkpoint()
        raise
    else:
        if checkpoint is not None:
            nested_error = _PENDING_CHECKPOINT_ROLLBACK_CAUSE
            if nested_error is not None:
                try:
                    _rollback_failed_checkpoint(
                        checkpoint,
                        previous_redo=previous_redo,
                    )
                except Exception as rollback_error:
                    raise CommandError(
                        _(
                            "Operation failed and its automatic rollback also failed. "
                            "The before-image remains available through `undo --force`.\n"
                            "Operation error: {operation_error}\n"
                            "Rollback error: {rollback_error}"
                        ).format(
                            operation_error=nested_error,
                            rollback_error=rollback_error,
                        )
                    ) from nested_error
                raise CommandError(
                    _(
                        "A nested transactional operation failed, so the "
                        "enclosing operation was rolled back: {error}"
                    ).format(error=nested_error)
                ) from nested_error
            finalize_pending_checkpoint()


def _rollback_failed_checkpoint(
    checkpoint: str,
    *,
    previous_redo: str | None,
) -> None:
    """Restore an incomplete checkpoint after its operation raises."""
    _clear_pending_checkpoint()

    if current_undo_commit() != checkpoint:
        raise CommandError(
            _("Cannot roll back a failed operation because its checkpoint moved.")
        )

    manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    validate_recovery_state(manifest)
    _restore_metadata_state(checkpoint, manifest)
    _undo_restore.restore_refs(manifest.get("refs", {}))
    _restore_index_state(manifest)
    _undo_restore.restore_worktree(checkpoint, manifest)
    _restore_intent_to_add_state(manifest)

    parent = checkpoint_parent(checkpoint)
    updates: list[tuple[str, str]] = []
    deletes: list[str] = []
    if parent is None:
        deletes.append(SESSION_UNDO_STACK_REF)
    else:
        updates.append((SESSION_UNDO_STACK_REF, parent))
    if previous_redo is None:
        deletes.append(SESSION_REDO_STACK_REF)
    else:
        updates.append((SESSION_REDO_STACK_REF, previous_redo))
    update_git_refs(updates=updates, deletes=deletes)


def finalize_pending_checkpoint() -> None:
    """Record the post-operation state for conflict detection."""
    checkpoint = _PENDING_CHECKPOINT
    if checkpoint is None:
        return
    _clear_pending_checkpoint()

    current = current_undo_commit()
    if current != checkpoint:
        raise CommandError(
            _(
                "Cannot finalize the undo checkpoint because its stack "
                "reference moved."
            )
        )

    try:
        manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    except CommandError as error:
        raise CommandError(
            _(
                "Cannot finalize the undo checkpoint because its before-image "
                "manifest is unavailable. The operation completed, but its "
                "checkpoint is incomplete."
            )
        ) from error

    paths = set(manifest.get("tracked_worktree_paths", []))
    if not _uses_explicit_worktree_scope(manifest):
        paths.update(_undo_worktree.changed_worktree_paths())
    paths = sorted(paths)
    index_paths = sorted(
        set(manifest.get("tracked_index_paths", manifest.get("tracked_worktree_paths", [])))
    )
    manifest["after"] = _snapshot_current_state(paths, index_paths=index_paths)
    manifest["after"]["tracked_index_paths"] = index_paths
    repository_paths = list(manifest.get("tracked_repository_paths", []))
    manifest["after"]["tracked_repository_paths"] = repository_paths
    manifest["after"]["repository_files"] = _filesystem_directory_state(
        get_git_directory_path(),
        relative_paths=repository_paths,
    )
    before_refs = manifest.get("refs", {})
    after_refs = manifest["after"].get("refs", {})
    tracked_refs = sorted(
        ref_name
        for ref_name in set(before_refs) | set(after_refs)
        if before_refs.get(ref_name) != after_refs.get(ref_name)
    )
    manifest["tracked_refs"] = tracked_refs
    manifest["refs"] = {
        ref_name: before_refs[ref_name]
        for ref_name in tracked_refs
        if ref_name in before_refs
    }
    manifest["after"]["tracked_refs"] = tracked_refs
    manifest["after"]["refs"] = {
        ref_name: after_refs[ref_name]
        for ref_name in tracked_refs
        if ref_name in after_refs
    }
    metadata_scopes = (
        ("session", get_session_directory_path()),
        ("batches", get_batches_directory_path()),
    )
    tree_removals: list[GitIndexEntryUpdate] = []
    for prefix, source_dir in metadata_scopes:
        before_files = _undo_restore.tree_prefix_state(checkpoint, prefix)
        after_files = _filesystem_directory_state(source_dir)
        tracked_paths = sorted(
            relative_path
            for relative_path in set(before_files) | set(after_files)
            if before_files.get(relative_path) != after_files.get(relative_path)
        )
        manifest[f"tracked_{prefix}_paths"] = tracked_paths
        manifest["after"][f"tracked_{prefix}_paths"] = tracked_paths
        manifest["after"][f"{prefix}_files"] = {
            relative_path: after_files[relative_path]
            for relative_path in tracked_paths
            if relative_path in after_files
        }
        tree_removals.extend(
            GitIndexEntryUpdate(
                file_path=f"{prefix}/{relative_path}",
                force_remove=True,
            )
            for relative_path in before_files
            if relative_path not in tracked_paths
        )
    manifest["after"]["worktree_paths"] = manifest["after"]["worktree_paths"]
    manifest["recovery_anchors"].update(anchor_recovery_state(manifest["after"]))
    retained_objects = state_recovery_objects(manifest)
    retained_objects.update(state_recovery_objects(manifest["after"]))
    manifest["recovery_anchors"] = {
        ref_name: object_name
        for ref_name, object_name in manifest["recovery_anchors"].items()
        if object_name in retained_objects
    }

    with temp_git_index() as env:
        git_read_tree(checkpoint, env=env)
        git_update_index_entries(tree_removals, env=env)
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
    normalized = {}
    for entry in entries:
        normalized_entry = entry
        if (
            entry.get("kind") == "gitlink"
            and entry.get("exists", False)
            and not entry.get("worktree_oid")
            and not entry.get("archive", False)
        ):
            # Before filesystem presence was recorded independently, an
            # index/HEAD-only gitlink used exists=True for an absent worktree.
            normalized_entry = {**entry, "exists": False}
        normalized[entry["path"]] = normalized_entry
    return normalized


def _detect_conflicts_against_state(expected_state: dict[str, Any]) -> list[str]:
    conflicts: list[str] = []
    current = _snapshot_current_state(
        [entry["path"] for entry in expected_state.get("worktree_paths", [])],
        index_paths=expected_state.get("tracked_index_paths"),
        ref_names=expected_state.get("tracked_refs"),
    )

    if "index_entries" in expected_state:
        index_changed = current.get("index_entries") != expected_state.get(
            "index_entries"
        )
    else:
        index_result = run_git_command(
            ["write-tree"],
            check=False,
            requires_index_lock=False,
        )
        index_changed = (
            index_result.stdout.strip() if index_result.returncode == 0 else None
        ) != expected_state.get("index_tree")
    if index_changed:
        conflicts.append(_("index"))

    if current.get("refs") != expected_state.get("refs"):
        conflicts.append(_("batch refs"))

    expected_worktree = _worktree_state_by_path(expected_state.get("worktree_paths", []))
    current_worktree = _worktree_state_by_path(current.get("worktree_paths", []))
    for path, expected in sorted(expected_worktree.items()):
        actual = current_worktree.get(path)
        if actual != expected:
            conflicts.append(path)

    for prefix, source_dir, label in (
        ("session", get_session_directory_path(), _("session state")),
        ("batches", get_batches_directory_path(), _("batch metadata")),
        ("repository", get_git_directory_path(), _("repository metadata")),
    ):
        tracked_paths = expected_state.get(f"tracked_{prefix}_paths")
        expected_files = expected_state.get(f"{prefix}_files")
        if isinstance(tracked_paths, list) and isinstance(expected_files, dict):
            conflict_paths = [
                path
                for path in tracked_paths
                if not (prefix == "session" and path.startswith("selected/"))
            ]
            current_files = _filesystem_directory_state(
                source_dir,
                relative_paths=conflict_paths,
            )
            expected_conflict_files = {
                path: expected_files[path]
                for path in conflict_paths
                if path in expected_files
            }
            if current_files != expected_conflict_files:
                conflicts.append(label)

    return conflicts


def _detect_conflicts(manifest: dict[str, Any]) -> list[str]:
    after = manifest.get("after")
    if not isinstance(after, dict):
        return [_('incomplete checkpoint')]
    return _detect_conflicts_against_state(after)


def _detect_redo_conflicts(manifest: dict[str, Any]) -> list[str]:
    after_undo = manifest.get("after_undo")
    if not isinstance(after_undo, dict):
        return [_('incomplete checkpoint')]
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
    if not _uses_explicit_worktree_scope(manifest):
        paths.update(_undo_worktree.changed_worktree_paths())
    return sorted(paths)


def _redo_relevant_index_paths(manifest: dict[str, Any]) -> list[str]:
    """Return index paths owned by an undo/redo checkpoint."""
    paths = set(
        manifest.get("tracked_index_paths", manifest.get("tracked_worktree_paths", []))
    )
    for state_name in ("after", "after_undo"):
        state = manifest.get(state_name)
        if isinstance(state, dict):
            paths.update(state.get("tracked_index_paths", []))
            paths.update(state.get("index_entries", {}))
    paths.update(manifest.get("index_entries", {}))
    return sorted(paths)


def _redo_relevant_refs(manifest: dict[str, Any]) -> list[str]:
    """Return refs owned by an undo/redo checkpoint."""
    refs = set(manifest.get("tracked_refs", []))
    for state_name in ("after", "after_undo"):
        state = manifest.get(state_name)
        if isinstance(state, dict):
            refs.update(state.get("tracked_refs", []))
    return sorted(refs)


def _restore_index_state(state: dict[str, Any]) -> None:
    """Restore scoped index entries, with legacy whole-tree compatibility."""
    index_entries = state.get("index_entries")
    if not isinstance(index_entries, dict):
        index_tree = state.get("index_tree")
        if index_tree:
            git_read_tree(index_tree)
        return

    if "tracked_index_paths" in state:
        scoped_paths = set(state.get("tracked_index_paths", []))
    else:
        scoped_paths = set(state.get("tracked_worktree_paths", []))
        for entry in state.get("worktree_paths", []):
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                scoped_paths.add(entry["path"])
    scoped_paths.update(index_entries)

    updates: list[GitIndexEntryUpdate] = []
    for file_path in sorted(scoped_paths):
        entry = index_entries.get(file_path)
        if isinstance(entry, dict):
            mode = entry.get("mode")
            object_id = entry.get("object_id")
            if isinstance(mode, str) and isinstance(object_id, str):
                updates.append(
                    GitIndexEntryUpdate(
                        file_path=file_path,
                        mode=mode,
                        blob_sha=object_id,
                    )
                )
                continue
        updates.append(GitIndexEntryUpdate(file_path=file_path, force_remove=True))
    git_update_index_entries(updates)


def _restore_metadata_state(commit: str, manifest: dict[str, Any]) -> None:
    """Restore scoped application state with legacy whole-directory support."""
    for prefix, target_dir in (
        ("session", get_session_directory_path()),
        ("batches", get_batches_directory_path()),
    ):
        tracked_paths = manifest.get(f"tracked_{prefix}_paths")
        if isinstance(tracked_paths, list):
            _undo_restore.restore_tree_paths(
                commit,
                prefix=prefix,
                target_dir=target_dir,
                tracked_paths=tracked_paths,
            )
        else:
            _undo_restore.restore_tree_prefix(
                commit,
                prefix=prefix,
                target_dir=target_dir,
            )
    repository_paths = manifest.get("tracked_repository_paths")
    if isinstance(repository_paths, list):
        _undo_restore.restore_tree_paths(
            commit,
            prefix="repository",
            target_dir=get_git_directory_path(),
            tracked_paths=repository_paths,
        )


def _write_snapshot_commit(
    *,
    ref_name: str,
    message: str,
    manifest: dict[str, Any],
    session_dir: Path,
    batches_dir: Path,
    repository_dir: Path,
    worktree_entries: list[dict[str, Any]],
    parent: str | None,
    session_paths: list[str] | None = None,
    batch_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
) -> str:
    with temp_git_index() as env:
        _add_blob_to_index(env, "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
        _add_directory_to_index(
            env,
            source_dir=session_dir,
            tree_prefix="session",
            relative_paths=session_paths,
        )
        _add_directory_to_index(
            env,
            source_dir=batches_dir,
            tree_prefix="batches",
            relative_paths=batch_paths,
        )
        _add_directory_to_index(
            env,
            source_dir=repository_dir,
            tree_prefix="repository",
            relative_paths=repository_paths,
        )

        repo_root = get_git_repository_root_path()
        index_updates: list[GitIndexEntryUpdate] = []
        for entry in worktree_entries:
            if (
                entry.get("kind") in {"gitlink", "embedded-repo"}
                and not entry.get("archive")
            ):
                continue
            if not entry.get("exists", False):
                continue
            blob_sha = entry.get("blob")
            if blob_sha:
                index_updates.append(
                    GitIndexEntryUpdate(
                        file_path=f"worktree/{entry['path']}",
                        mode=entry.get("storage_mode", entry.get("mode", "100644")),
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
    target_repository_dir: Path,
    after_undo: dict[str, Any],
    worktree_entries: list[dict[str, Any]],
    session_paths: list[str],
    batch_paths: list[str],
    repository_paths: list[str],
) -> str:
    recovery_objects = state_recovery_objects(target)
    recovery_objects.update(state_recovery_objects(after_undo))
    recovery_objects.add(undo_checkpoint)
    manifest = {
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
        "worktree_paths": [
            {key: value for key, value in entry.items() if key != "blob"}
            for entry in worktree_entries
        ],
        "after_undo": after_undo,
        "recovery_anchors": anchor_recovery_objects(recovery_objects),
    }

    parent = current_redo_commit()
    return _write_snapshot_commit(
        ref_name=SESSION_REDO_STACK_REF,
        message=f"Redo node: {operation}",
        manifest=manifest,
        session_dir=target_session_dir,
        batches_dir=target_batches_dir,
        repository_dir=target_repository_dir,
        worktree_entries=worktree_entries,
        parent=parent,
        session_paths=session_paths,
        batch_paths=batch_paths,
        repository_paths=repository_paths,
    )


def _copy_tracked_repository_files(
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


def undo_last_checkpoint(*, force: bool = False) -> str:
    """Restore the latest undo checkpoint and pop it from the undo stack."""
    finalize_pending_checkpoint()
    checkpoint = current_undo_commit()
    if checkpoint is None:
        raise CommandError(_("Nothing to undo."))

    manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    validate_recovery_state(manifest)
    after = manifest.get("after")
    if isinstance(after, dict):
        validate_recovery_state(after)
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
    redo_index_paths = _redo_relevant_index_paths(manifest)
    redo_refs = _redo_relevant_refs(manifest)
    redo_target = _snapshot_current_state(
        redo_paths,
        index_paths=redo_index_paths,
        ref_names=redo_refs,
    )
    redo_target["tracked_index_paths"] = redo_index_paths
    redo_target["tracked_refs"] = redo_refs
    redo_worktree_entries = _undo_worktree.snapshot_worktree_paths(redo_paths)

    redo_session_dir = tempfile.mkdtemp(prefix="gsb-redo-session-")
    redo_batches_dir = tempfile.mkdtemp(prefix="gsb-redo-batches-")
    redo_repository_dir = tempfile.mkdtemp(prefix="gsb-redo-repository-")
    try:
        live_session_dir = get_session_directory_path()
        live_batches_dir = get_batches_directory_path()
        if live_session_dir.exists():
            shutil.copytree(live_session_dir, redo_session_dir, dirs_exist_ok=True)
        if live_batches_dir.exists():
            shutil.copytree(live_batches_dir, redo_batches_dir, dirs_exist_ok=True)
        repository_paths = list(manifest.get("tracked_repository_paths", []))
        _copy_tracked_repository_files(
            get_git_directory_path(),
            Path(redo_repository_dir),
            repository_paths,
        )

        _restore_metadata_state(checkpoint, manifest)
        _undo_restore.restore_refs(
            manifest.get("refs", {}),
            tracked_refs=manifest.get("tracked_refs"),
        )

        _restore_index_state(manifest)

        _undo_restore.restore_worktree(checkpoint, manifest)
        _restore_intent_to_add_state(manifest)

        after_undo = _snapshot_current_state(
            redo_paths,
            index_paths=redo_index_paths,
            ref_names=redo_refs,
        )
        after_undo["tracked_index_paths"] = redo_index_paths
        after_undo["tracked_refs"] = redo_refs
        session_paths = list(manifest.get("tracked_session_paths", []))
        batch_paths = list(manifest.get("tracked_batches_paths", []))
        after_undo["tracked_session_paths"] = session_paths
        after_undo["tracked_batches_paths"] = batch_paths
        after_undo["tracked_repository_paths"] = repository_paths
        after_undo["session_files"] = _filesystem_directory_state(
            get_session_directory_path(),
            relative_paths=session_paths,
        )
        after_undo["batches_files"] = _filesystem_directory_state(
            get_batches_directory_path(),
            relative_paths=batch_paths,
        )
        after_undo["repository_files"] = _filesystem_directory_state(
            get_git_directory_path(),
            relative_paths=repository_paths,
        )

        _push_redo_node(
            operation=operation,
            undo_checkpoint=checkpoint,
            target=redo_target,
            target_session_dir=Path(redo_session_dir),
            target_batches_dir=Path(redo_batches_dir),
            target_repository_dir=Path(redo_repository_dir),
            after_undo=after_undo,
            worktree_entries=redo_worktree_entries,
            session_paths=session_paths,
            batch_paths=batch_paths,
            repository_paths=repository_paths,
        )
    finally:
        shutil.rmtree(redo_session_dir, ignore_errors=True)
        shutil.rmtree(redo_batches_dir, ignore_errors=True)
        shutil.rmtree(redo_repository_dir, ignore_errors=True)

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
    validate_recovery_state(manifest)
    after_undo = manifest.get("after_undo")
    if isinstance(after_undo, dict):
        validate_recovery_state(after_undo)
    conflicts = _detect_redo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot redo because current state has changed since the undo: {items}.\n"
              "Run 'git-stage-batch redo --force' to overwrite those changes.").format(items=preview)
        )

    _restore_metadata_state(redo_node, manifest)
    _undo_restore.restore_refs(
        manifest.get("refs", {}),
        tracked_refs=manifest.get("tracked_refs"),
    )

    _restore_index_state(manifest)

    _undo_restore.restore_worktree(redo_node, manifest)
    _restore_intent_to_add_state(manifest)

    undo_checkpoint = manifest.get("undo_checkpoint")
    if undo_checkpoint:
        update_git_refs(updates=[(SESSION_UNDO_STACK_REF, undo_checkpoint)])

    parent = checkpoint_parent(redo_node)
    if parent:
        update_git_refs(updates=[(SESSION_REDO_STACK_REF, parent)])
    else:
        update_git_refs(deletes=[SESSION_REDO_STACK_REF])

    return str(manifest.get("operation", "operation"))
