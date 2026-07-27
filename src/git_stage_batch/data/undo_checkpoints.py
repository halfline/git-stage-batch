"""Legacy undo and redo stack entry points."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .undo import restore as _undo_restore
from .undo import snapshots as _undo_snapshots
from .undo import state as _undo_state
from .undo import worktree as _undo_worktree
from .undo.checkpoints import finalize_pending_checkpoint
from .undo.checkpoints import undo_checkpoint as undo_checkpoint
from .undo.refs import (
    SESSION_REDO_STACK_REF,
    SESSION_UNDO_STACK_REF,
    checkpoint_parent,
    current_redo_commit,
    current_undo_commit,
)
from .recovery_anchors import validate_recovery_state
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_refs import update_git_refs
from ..utils.git_repository import get_git_directory_path
from ..utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
)


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
    conflicts = _undo_state.detect_undo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot undo because current state has changed since the checkpoint: {items}.\n"
              "Run 'git-stage-batch undo --force' to overwrite those changes.").format(items=preview)
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
    if isinstance(after_undo, dict):
        validate_recovery_state(after_undo)
    conflicts = _undo_state.detect_redo_conflicts(manifest)
    if conflicts and not force:
        preview = ", ".join(conflicts[:5])
        if len(conflicts) > 5:
            preview = _("{preview}, and {count} more").format(preview=preview, count=len(conflicts) - 5)
        raise CommandError(
            _("Cannot redo because current state has changed since the undo: {items}.\n"
              "Run 'git-stage-batch redo --force' to overwrite those changes.").format(items=preview)
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
