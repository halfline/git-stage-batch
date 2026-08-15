"""Undo checkpoint stack orchestration."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

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
    current_stack_commit,
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
_TRANSIENT_TRANSACTION_REF_PREFIX = "refs/git-stage-batch/transactions/"


RollbackStatus: TypeAlias = Literal[
    "unavailable",
    "not-requested",
    "pending",
    "delegated",
    "completed",
    "failed",
    "not-needed",
    "not-attempted",
]


@dataclass(frozen=True, slots=True)
class _DeferredTransactionCompletion:
    """Paired effects for one command completed inside a transaction."""

    on_commit: Callable[[], None]
    on_rollback: Callable[[RollbackStatus], None] | None = None


@dataclass(slots=True)
class _TransactionBoundary:
    """Shared publication lifecycle for nested transactional contexts."""

    armed: bool = True
    completions: list[_DeferredTransactionCompletion] = field(default_factory=list)
    outcome: Literal["pending", "committed", "rolled-back"] = "pending"
    rollback: RollbackStatus | None = None

    def defer_success(self, callback: Callable[[], None]) -> None:
        """Run after the outermost commit, or immediately once committed."""
        self.defer_completion(callback)

    def defer_completion(
        self,
        on_commit: Callable[[], None],
        on_rollback: Callable[[RollbackStatus], None] | None = None,
    ) -> None:
        """Run paired completion effects at the outermost outcome."""
        if self.outcome == "committed":
            on_commit()
        elif self.outcome == "rolled-back":
            if on_rollback is not None and self.rollback is not None:
                on_rollback(self.rollback)
        elif self.outcome == "pending":
            self.completions.append(
                _DeferredTransactionCompletion(on_commit, on_rollback)
            )

    def commit(self) -> None:
        """Publish every deferred success effect after durable completion."""
        self.outcome = "committed"
        completions = tuple(self.completions)
        self.completions.clear()
        first_error: BaseException | None = None
        for completion in completions:
            try:
                completion.on_commit()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def finish_rollback(self, rollback: RollbackStatus) -> None:
        """Publish best-effort rollback effects after restoration settles."""
        self.outcome = "rolled-back"
        self.rollback = rollback
        completions = tuple(self.completions)
        self.completions.clear()
        for completion in completions:
            if completion.on_rollback is None:
                continue
            try:
                completion.on_rollback(rollback)
            except BaseException:
                # A diagnostic completion must not mask the publication or
                # rollback failure that brought the transaction here.
                pass


@dataclass(slots=True)
class UndoCheckpointStatus:
    """Observable rollback state for one checkpoint context.

    A delegated rollback belongs to an enclosing transactional checkpoint;
    the nested context cannot report that enclosing rollback's final outcome.
    """

    rollback: RollbackStatus = "not-requested"
    _transaction_boundary: _TransactionBoundary = field(
        default_factory=_TransactionBoundary
    )
    _context_rollback_armed: bool = True

    @property
    def _rollback_armed(self) -> bool:
        """Return whether this context's shared publication has started."""
        return self._transaction_boundary.armed

    def arm_rollback(self) -> None:
        """Mark the beginning of caller-owned publication mutations."""
        self._context_rollback_armed = True
        self._transaction_boundary.armed = True

    def defer_success(self, callback: Callable[[], None]) -> None:
        """Run a success effect after the outermost transaction commits."""
        self._transaction_boundary.defer_success(callback)

    def defer_completion(
        self,
        on_commit: Callable[[], None],
        on_rollback: Callable[[RollbackStatus], None],
    ) -> None:
        """Run paired effects after the outermost transaction settles."""
        self._transaction_boundary.defer_completion(on_commit, on_rollback)




def _clear_pending_checkpoint() -> None:
    """Forget process-local state for the pending checkpoint."""
    global _PENDING_CHECKPOINT, _PENDING_CHECKPOINT_REPOSITORY
    global _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR, _PENDING_CHECKPOINT_ROLLBACK_CAUSE
    global _PENDING_CHECKPOINT_TRANSACTION_BOUNDARY
    _PENDING_CHECKPOINT = None
    _PENDING_CHECKPOINT_REPOSITORY = None
    _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR = None
    _PENDING_CHECKPOINT_ROLLBACK_CAUSE = None
    _PENDING_CHECKPOINT_TRANSACTION_BOUNDARY = None


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
    session_files = _undo_snapshots.filesystem_directory_state(session_dir)
    batches_files = _undo_snapshots.filesystem_directory_state(
        get_batches_directory_path()
    )
    repository_files = _undo_snapshots.filesystem_directory_state(
        get_git_directory_path(),
        relative_paths=tracked_repository_paths,
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
        "session_files": session_files,
        "batches_files": batches_files,
        "repository_files": repository_files,
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
            filesystem_state=session_files,
        )
        _undo_snapshots.add_directory_to_index(
            env,
            source_dir=get_batches_directory_path(),
            tree_prefix="batches",
            filesystem_state=batches_files,
        )
        if tracked_repository_paths:
            _undo_snapshots.add_directory_to_index(
                env,
                source_dir=get_git_directory_path(),
                tree_prefix="repository",
                relative_paths=tracked_repository_paths,
                filesystem_state=repository_files,
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


def _create_transient_transaction_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str],
    repository_paths: list[str] | None,
) -> tuple[str, str, CheckpointState]:
    """Create a Git-backed before-image without publishing session undo state."""
    tracked_worktree_paths = sorted(set(worktree_paths))
    tracked_index_paths = sorted(set(index_paths))
    tracked_repository_paths = sorted(set(repository_paths or []))
    before = _undo_snapshots.snapshot_current_state(
        tracked_worktree_paths,
        index_paths=tracked_index_paths,
        ref_names=[],
    )
    repository_files = _undo_snapshots.filesystem_directory_state(
        get_git_directory_path(),
        relative_paths=tracked_repository_paths,
    )
    worktree_entries = before["worktree_paths"]
    manifest: CheckpointState = {
        "operation": display_path(operation),
        "index_entries": before["index_entries"],
        "intent_to_add_paths": before["intent_to_add_paths"],
        "refs": {},
        "worktree_paths": [
            worktree_metadata_without_blob(entry) for entry in worktree_entries
        ],
        "tracked_worktree_paths": tracked_worktree_paths,
        "tracked_index_paths": tracked_index_paths,
        "tracked_refs": [],
        "tracked_session_paths": [],
        "tracked_batches_paths": [],
        "tracked_repository_paths": tracked_repository_paths,
        "session_files": {},
        "batches_files": {},
        "repository_files": repository_files,
        "worktree_path_scope": _undo_state.EXPLICIT_WORKTREE_SCOPE,
    }
    ref_name = _TRANSIENT_TRANSACTION_REF_PREFIX + uuid4().hex
    try:
        checkpoint = _undo_snapshots.write_snapshot_commit(
            ref_name=ref_name,
            message=f"Transient transaction checkpoint: {display_path(operation)}",
            manifest=manifest,
            session_dir=get_session_directory_path(),
            batches_dir=get_batches_directory_path(),
            repository_dir=get_git_directory_path(),
            worktree_entries=worktree_entries,
            parent=None,
            session_paths=[],
            batch_paths=[],
            repository_paths=tracked_repository_paths,
            index_entries=before["index_entries"],
        )
    except BaseException:
        try:
            update_git_refs(deletes=[ref_name])
        except BaseException:
            # Preserve the snapshot error; a successfully published ref is a
            # usable recovery root if best-effort cleanup itself fails.
            pass
        raise
    return ref_name, checkpoint, manifest


def _restore_transient_transaction_checkpoint(
    ref_name: str,
    checkpoint: str,
    manifest: CheckpointState,
) -> None:
    """Restore one transient before-image after a failed publication."""
    if current_stack_commit(ref_name) != checkpoint:
        raise CommandError(
            _("Cannot roll back a failed operation because its checkpoint moved.")
        )
    validate_recovery_state(manifest)
    _undo_state.restore_checkpoint_state(checkpoint, manifest)


def _delete_transient_transaction_ref(ref_name: str) -> None:
    """Release a transient checkpoint after success or completed rollback."""
    update_git_refs(deletes=[ref_name])

@contextmanager
def transaction_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
) -> Iterator[UndoCheckpointStatus]:
    """Defer transaction completion effects at one publication boundary."""
    status = UndoCheckpointStatus(
        rollback="pending",
        _transaction_boundary=_TransactionBoundary(armed=False),
        _context_rollback_armed=False,
    )
    try:
        yield status
    except BaseException:
        status.rollback = (
            "not-attempted" if status._rollback_armed else "not-needed"
        )
        status._transaction_boundary.finish_rollback(status.rollback)
        raise
    else:
        status.rollback = "not-needed"
        status._transaction_boundary.commit()


@contextmanager
def undo_checkpoint(
    operation: str,
    *,
    worktree_paths: list[str],
    index_paths: list[str] | None = None,
    repository_paths: list[str] | None = None,
    rollback_on_error: bool = False,
    defer_rollback_until_armed: bool = False,
) -> Iterator[UndoCheckpointStatus]:
    """Bracket an undoable operation with before and after snapshots.

    When rollback_on_error is true, restore the before-image and discard the
    checkpoint before propagating an exception. This turns the checkpoint into
    a transaction boundary for commands that span several files or stores.
    """
    global _PENDING_CHECKPOINT_ROLLBACK_ON_ERROR
    global _PENDING_CHECKPOINT_ROLLBACK_CAUSE
    global _PENDING_CHECKPOINT_TRANSACTION_BOUNDARY
    status = UndoCheckpointStatus(
        _transaction_boundary=_TransactionBoundary(
            armed=not defer_rollback_until_armed,
        ),
        _context_rollback_armed=not defer_rollback_until_armed,
    )

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
        _PENDING_CHECKPOINT_TRANSACTION_BOUNDARY = status._transaction_boundary
        if rollback_on_error:
            status.rollback = "pending"
    elif rollback_on_error:
        status.rollback = "unavailable"
    try:
        yield status
    except BaseException as operation_error:
        if checkpoint is not None and rollback_on_error and not status._rollback_armed:
            try:
                _discard_failed_checkpoint(
                    checkpoint,
                    previous_redo=previous_redo,
                )
            except BaseException:
                status.rollback = "failed"
                status._transaction_boundary.finish_rollback(status.rollback)
                raise
            status.rollback = "not-needed"
            status._transaction_boundary.finish_rollback(status.rollback)
        elif checkpoint is not None and rollback_on_error:
            try:
                _rollback_failed_checkpoint(
                    checkpoint,
                    previous_redo=previous_redo,
                )
            except BaseException as rollback_error:
                status.rollback = "failed"
                status._transaction_boundary.finish_rollback(status.rollback)
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
            status._transaction_boundary.finish_rollback(status.rollback)
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
        elif rollback_on_error:
            status._transaction_boundary.finish_rollback(status.rollback)
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
                    status._transaction_boundary.finish_rollback(status.rollback)
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
                status._transaction_boundary.finish_rollback(status.rollback)
                raise CommandError(
                    _(
                        "A nested transactional operation failed, so the "
                        "enclosing operation was rolled back: {error}"
                    ).format(error=pending_nested_error)
                ) from pending_nested_error
            try:
                finalize_pending_checkpoint()
            except BaseException as finalization_error:
                if rollback_on_error and status._rollback_armed:
                    _rollback_transaction_checkpoint(
                        checkpoint,
                        previous_redo=previous_redo,
                        operation_error=finalization_error,
                        status=status,
                    )
                else:
                    status.rollback = "not-attempted"
                    if rollback_on_error:
                        status._transaction_boundary.finish_rollback(status.rollback)
                raise
            if rollback_on_error:
                status.rollback = "not-needed"
                status._transaction_boundary.commit()


def _rollback_transaction_checkpoint(
    checkpoint: str,
    *,
    previous_redo: str | None,
    operation_error: BaseException,
    status: UndoCheckpointStatus,
) -> None:
    """Restore a transactional before-image or report both failures."""
    try:
        _rollback_failed_checkpoint(
            checkpoint,
            previous_redo=previous_redo,
        )
    except BaseException as rollback_error:
        status.rollback = "failed"
        status._transaction_boundary.finish_rollback(status.rollback)
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
    status._transaction_boundary.finish_rollback(status.rollback)


def _discard_failed_checkpoint(
    checkpoint: str,
    *,
    previous_redo: str | None,
) -> None:
    """Drop an unarmed checkpoint without restoring its target snapshots."""
    _clear_pending_checkpoint()

    if current_undo_commit() != checkpoint:
        raise CommandError(
            _("Cannot roll back a failed operation because its checkpoint moved.")
        )

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
    _discard_failed_checkpoint(
        checkpoint,
        previous_redo=previous_redo,
    )


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
        ("session", "session_files", get_session_directory_path()),
        ("batches", "batches_files", get_batches_directory_path()),
    )
    tree_removals: list[GitIndexEntryUpdate] = []
    for prefix, state_field, source_dir in metadata_scopes:
        saved_files = manifest.get(state_field)
        before_files = (
            saved_files
            if isinstance(saved_files, dict)
            else _undo_restore.tree_prefix_state(checkpoint, prefix)
        )
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
        before_metadata_files = {
            relative_path: before_files[relative_path]
            for relative_path in metadata_tracked_paths
            if relative_path in before_files
        }
        if prefix == "session":
            manifest["session_files"] = before_metadata_files
            manifest["tracked_session_paths"] = metadata_tracked_paths
            after["tracked_session_paths"] = metadata_tracked_paths
            after["session_files"] = metadata_files
        else:
            manifest["batches_files"] = before_metadata_files
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
    session_paths = list(manifest.get("tracked_session_paths", []))
    batch_paths = list(manifest.get("tracked_batches_paths", []))
    repository_paths = list(manifest.get("tracked_repository_paths", []))
    redo_target["tracked_session_paths"] = session_paths
    redo_target["tracked_batches_paths"] = batch_paths
    redo_target["tracked_repository_paths"] = repository_paths
    redo_target["session_files"] = _undo_snapshots.filesystem_directory_state(
        get_session_directory_path(),
        relative_paths=session_paths,
    )
    redo_target["batches_files"] = _undo_snapshots.filesystem_directory_state(
        get_batches_directory_path(),
        relative_paths=batch_paths,
    )
    redo_target["repository_files"] = _undo_snapshots.filesystem_directory_state(
        get_git_directory_path(),
        relative_paths=repository_paths,
    )
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
