"""Undo checkpoint stack orchestration."""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from . import restore as _undo_restore
from . import snapshots as _undo_snapshots
from . import state as _undo_state
from . import worktree as _undo_worktree
from ..recovery_types import CheckpointState, worktree_metadata_without_blob
from .refs import (
    SESSION_REDO_STACK_REF,
    SESSION_UNDO_STACK_REF,
    checkpoint_parent,
    current_redo_commit,
    current_undo_commit,
)
from ..recovery_anchors import (
    anchor_recovery_state,
    state_recovery_objects,
    validate_recovery_state,
)
from ...utils.session_start_point import current_head_commit
from ...exceptions import CommandError
from ...git_paths import display_path
from ...i18n import _, ngettext
from ...utils.git_refs import (
    update_git_refs,
)
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_commit_tree,
    git_read_tree,
    git_update_index_entries,
    git_write_tree,
    temp_git_index,
)
from ...utils.git_repository import get_git_directory_path
from ...utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
    get_state_directory_path,
)


_PENDING_CHECKPOINT: str | None = None
_PENDING_CHECKPOINT_REPOSITORY: Path | None = None
_PENDING_CHECKPOINT_ROLLBACK_ON_ERROR: bool | None = None
_PENDING_CHECKPOINT_ROLLBACK_CAUSE: BaseException | None = None


@dataclass(slots=True)
class UndoCheckpointStatus:
    """Observable rollback state for one checkpoint context.

    A delegated rollback belongs to an enclosing transactional checkpoint;
    the nested context cannot report that enclosing rollback's final outcome.
    """

    rollback: Literal[
        "unavailable",
        "not-requested",
        "pending",
        "delegated",
        "completed",
        "failed",
        "not-needed",
        "not-attempted",
    ] = "not-requested"


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
                ngettext(
                    "Cannot start nested undoable operation because the outer "
                    "checkpoint does not cover this {scope} path: {paths}",
                    "Cannot start nested undoable operation because the outer "
                    "checkpoint does not cover these {scope} paths: {paths}",
                    len(missing_paths),
                ).format(
                    scope=scope_name,
                    paths=", ".join(display_path(path) for path in missing_paths),
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
) -> tuple[Literal["explicit"], list[str]]:
    """Return checkpoint scope metadata and paths to snapshot."""
    return _undo_state.EXPLICIT_WORKTREE_SCOPE, sorted(set(worktree_paths))


def _create_undo_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
) -> str | None:
    """Create a before-image checkpoint for an undoable operation."""
    operation = display_path(operation)
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
    before = _undo_snapshots.snapshot_current_state(
        tracked_worktree_paths,
        index_paths=tracked_index_paths,
    )
    recovery_anchors = anchor_recovery_state(before)

    manifest: CheckpointState = {
        "operation": operation,
        "head": current_head_commit(),
        "index_entries": before["index_entries"],
        "intent_to_add_paths": before["intent_to_add_paths"],
        "refs": before["refs"],
        "worktree_paths": [
            worktree_metadata_without_blob(entry)
            for entry in before["worktree_paths"]
        ],
        "tracked_worktree_paths": tracked_worktree_paths,
        "tracked_index_paths": tracked_index_paths,
        "tracked_repository_paths": tracked_repository_paths,
        "worktree_path_scope": worktree_path_scope,
        "recovery_anchors": recovery_anchors,
    }

    with temp_git_index() as env:
        _undo_snapshots.add_blob_to_index(
            env,
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        _undo_snapshots.add_directory_to_index(
            env,
            source_dir=session_dir,
            tree_prefix="session",
        )
        _undo_snapshots.add_directory_to_index(
            env,
            source_dir=get_batches_directory_path(),
            tree_prefix="batches",
        )
        if tracked_repository_paths:
            _undo_snapshots.add_directory_to_index(
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
) -> Iterator[UndoCheckpointStatus]:
    """Bracket an undoable operation with before and after snapshots.

    When rollback_on_error is true, restore the before-image and discard the
    checkpoint before propagating an exception. This turns the checkpoint into
    a transaction boundary for commands that span several files or stores.
    """
    global _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR
    global _PENDING_CHECKPOINT_ROLLBACK_CAUSE
    status = UndoCheckpointStatus()

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
            if rollback_on_error:
                # The enclosing transaction owns the shared before-image and
                # will decide whether rollback succeeds.  Do not report this
                # nested operation as independently pending or completed.
                status.rollback = "delegated"
            try:
                yield status
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
        if rollback_on_error:
            status.rollback = "pending"
    elif rollback_on_error:
        status.rollback = "unavailable"
    try:
        yield status
    except BaseException as operation_error:
        if checkpoint is not None and rollback_on_error:
            try:
                _rollback_failed_checkpoint(
                    checkpoint,
                    previous_redo=previous_redo,
                )
            except BaseException as rollback_error:
                status.rollback = "failed"
                if not isinstance(rollback_error, Exception):
                    raise
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
            status.rollback = "completed"
        elif checkpoint is not None:
            try:
                finalize_pending_checkpoint()
            except BaseException as finalization_error:
                status.rollback = "not-attempted"
                if not isinstance(finalization_error, Exception):
                    raise
                if not isinstance(operation_error, Exception):
                    raise operation_error from finalization_error
                raise CommandError(
                    _(
                        "Operation failed and its undo checkpoint could not be "
                        "finalized.\n"
                        "Operation error: {operation_error}\n"
                        "Finalization error: {finalization_error}"
                    ).format(
                        operation_error=operation_error,
                        finalization_error=finalization_error,
                    )
                ) from operation_error
        raise
    else:
        if checkpoint is not None:
            pending_nested_error = _PENDING_CHECKPOINT_ROLLBACK_CAUSE
            if pending_nested_error is not None:
                try:
                    _rollback_failed_checkpoint(
                        checkpoint,
                        previous_redo=previous_redo,
                    )
                except BaseException as rollback_error:
                    status.rollback = "failed"
                    if not isinstance(rollback_error, Exception):
                        raise
                    raise CommandError(
                        _(
                            "Operation failed and its automatic rollback also failed. "
                            "The before-image remains available through `undo --force`.\n"
                            "Operation error: {operation_error}\n"
                            "Rollback error: {rollback_error}"
                        ).format(
                            operation_error=pending_nested_error,
                            rollback_error=rollback_error,
                        )
                    ) from pending_nested_error
                status.rollback = "completed"
                raise CommandError(
                    _(
                        "A nested transactional operation failed, so the "
                        "enclosing operation was rolled back: {error}"
                    ).format(error=pending_nested_error)
                ) from pending_nested_error
            try:
                finalize_pending_checkpoint()
            except BaseException:
                status.rollback = "not-attempted"
                raise
            if rollback_on_error:
                status.rollback = "not-needed"


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
    _undo_state.restore_checkpoint_state(checkpoint, manifest)

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
            _("Cannot finalize the undo checkpoint because its stack reference moved.")
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

    tracked_paths = set(manifest.get("tracked_worktree_paths", []))
    if not _undo_state.uses_explicit_worktree_scope(manifest):
        tracked_paths.update(_undo_worktree.changed_worktree_paths())
    snapshot_paths = sorted(tracked_paths)
    index_paths = sorted(
        set(
            manifest.get(
                "tracked_index_paths", manifest.get("tracked_worktree_paths", [])
            )
        )
    )
    after = _undo_snapshots.snapshot_current_state(
        snapshot_paths,
        index_paths=index_paths,
    )
    manifest["after"] = after
    after["tracked_index_paths"] = index_paths
    repository_paths = list(manifest.get("tracked_repository_paths", []))
    after["tracked_repository_paths"] = repository_paths
    after["repository_files"] = _undo_snapshots.filesystem_directory_state(
        get_git_directory_path(),
        relative_paths=repository_paths,
    )
    before_refs = manifest.get("refs", {})
    after_refs = after.get("refs", {})
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
    after["tracked_refs"] = tracked_refs
    after["refs"] = {
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
        after_files = _undo_snapshots.filesystem_directory_state(source_dir)
        metadata_tracked_paths = sorted(
            relative_path
            for relative_path in set(before_files) | set(after_files)
            if before_files.get(relative_path) != after_files.get(relative_path)
        )
        metadata_files = {
            relative_path: after_files[relative_path]
            for relative_path in metadata_tracked_paths
            if relative_path in after_files
        }
        if prefix == "session":
            manifest["tracked_session_paths"] = metadata_tracked_paths
            after["tracked_session_paths"] = metadata_tracked_paths
            after["session_files"] = metadata_files
        else:
            manifest["tracked_batches_paths"] = metadata_tracked_paths
            after["tracked_batches_paths"] = metadata_tracked_paths
            after["batches_files"] = metadata_files
        tree_removals.extend(
            GitIndexEntryUpdate(
                file_path=f"{prefix}/{relative_path}",
                force_remove=True,
            )
            for relative_path in before_files
            if relative_path not in metadata_tracked_paths
        )
    manifest["recovery_anchors"].update(anchor_recovery_state(after))
    retained_objects = state_recovery_objects(manifest)
    retained_objects.update(state_recovery_objects(after))
    manifest["recovery_anchors"] = {
        ref_name: object_name
        for ref_name, object_name in manifest["recovery_anchors"].items()
        if object_name in retained_objects
    }

    with temp_git_index() as env:
        git_read_tree(checkpoint, env=env)
        git_update_index_entries(tree_removals, env=env)
        _undo_snapshots.add_blob_to_index(
            env,
            "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        tree_sha = git_write_tree(env=env)

    parent = checkpoint_parent(checkpoint)
    checkpoint_commit = git_commit_tree(
        tree_sha,
        parents=[parent] if parent else [],
        message=f"Undo checkpoint: {manifest.get('operation', 'operation')}",
    )
    update_git_refs(updates=[(SESSION_UNDO_STACK_REF, checkpoint_commit)])


def undo_last_checkpoint(*, force: bool = False) -> str:
    """Restore the latest undo checkpoint and pop it from the undo stack."""
    finalize_pending_checkpoint()
    checkpoint = current_undo_commit()
    if checkpoint is None:
        raise CommandError(_("Nothing to undo."))

    manifest = _undo_restore.read_json_from_commit(checkpoint, "manifest.json")
    validate_recovery_state(manifest)
    after = manifest.get("after")
    if after is not None:
        validate_recovery_state(after)
    conflicts = _undo_state.detect_undo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            remaining = len(conflicts) - 5
            preview = ngettext(
                "{preview}, and {count} more conflict",
                "{preview}, and {count} more conflicts",
                remaining,
            ).format(preview=preview, count=remaining)
        raise CommandError(
            _(
                "Cannot undo because current state has changed since the checkpoint: {items}.\n"
                "Run 'git-stage-batch undo --force' to overwrite those changes."
            ).format(items=preview)
        )

    operation = str(manifest.get("operation", "operation"))
    redo_paths = _undo_state.redo_relevant_paths(manifest)
    redo_index_paths = _undo_state.redo_relevant_index_paths(manifest)
    redo_refs = _undo_state.redo_relevant_refs(manifest)
    redo_target = _undo_snapshots.snapshot_current_state(
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
        _undo_snapshots.copy_tracked_repository_files(
            get_git_directory_path(),
            Path(redo_repository_dir),
            repository_paths,
        )

        _undo_state.restore_checkpoint_state(checkpoint, manifest)

        after_undo = _undo_snapshots.snapshot_current_state(
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
        after_undo["session_files"] = _undo_snapshots.filesystem_directory_state(
            get_session_directory_path(),
            relative_paths=session_paths,
        )
        after_undo["batches_files"] = _undo_snapshots.filesystem_directory_state(
            get_batches_directory_path(),
            relative_paths=batch_paths,
        )
        after_undo["repository_files"] = _undo_snapshots.filesystem_directory_state(
            get_git_directory_path(),
            relative_paths=repository_paths,
        )

        _undo_snapshots.push_redo_node(
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
    if after_undo is not None:
        validate_recovery_state(after_undo)
    conflicts = _undo_state.detect_redo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            remaining = len(conflicts) - 5
            preview = ngettext(
                "{preview}, and {count} more conflict",
                "{preview}, and {count} more conflicts",
                remaining,
            ).format(preview=preview, count=remaining)
        raise CommandError(
            _(
                "Cannot redo because current state has changed since the undo: {items}.\n"
                "Run 'git-stage-batch redo --force' to overwrite those changes."
            ).format(items=preview)
        )

    _undo_state.restore_checkpoint_state(redo_node, manifest)

    undo_checkpoint = manifest.get("undo_checkpoint")
    if undo_checkpoint:
        update_git_refs(updates=[(SESSION_UNDO_STACK_REF, undo_checkpoint)])

    parent = checkpoint_parent(redo_node)
    if parent:
        update_git_refs(updates=[(SESSION_REDO_STACK_REF, parent)])
    else:
        update_git_refs(deletes=[SESSION_REDO_STACK_REF])

    return str(manifest.get("operation", "operation"))
