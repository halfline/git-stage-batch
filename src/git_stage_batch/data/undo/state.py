"""Undo checkpoint state comparison and restoration policy."""

from __future__ import annotations

from typing import Literal

from . import restore as _undo_restore
from . import snapshots as _undo_snapshots
from . import worktree as _undo_worktree
from ..recovery_types import CheckpointState, WorktreePathState
from ...i18n import _
from ...utils.git_command import run_git_command
from ...utils.git_index import (
    GitIndexEntryUpdate,
    git_read_tree,
    git_update_index_entries,
)
from ...utils.git_repository import get_git_directory_path
from ...utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
)


EXPLICIT_WORKTREE_SCOPE: Literal["explicit"] = "explicit"


def uses_explicit_worktree_scope(manifest: CheckpointState) -> bool:
    """Return whether a checkpoint intentionally scoped worktree snapshots."""
    return manifest.get("worktree_path_scope") == EXPLICIT_WORKTREE_SCOPE


def restore_intent_to_add_state(state: CheckpointState) -> None:
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


def _worktree_state_by_path(
    entries: list[WorktreePathState],
) -> dict[str, WorktreePathState]:
    normalized: dict[str, WorktreePathState] = {}
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


def _detect_conflicts_against_state(
    expected_state: CheckpointState,
) -> list[str]:
    conflicts: list[str] = []
    current = _undo_snapshots.snapshot_current_state(
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

    expected_worktree = _worktree_state_by_path(
        expected_state.get("worktree_paths", [])
    )
    current_worktree = _worktree_state_by_path(current.get("worktree_paths", []))
    for path, expected in sorted(expected_worktree.items()):
        actual = current_worktree.get(path)
        if actual != expected:
            conflicts.append(path)

    for tracked_paths, expected_files, source_dir, label, ignore_selected in (
        (
            expected_state.get("tracked_session_paths"),
            expected_state.get("session_files"),
            get_session_directory_path(),
            _("session state"),
            True,
        ),
        (
            expected_state.get("tracked_batches_paths"),
            expected_state.get("batches_files"),
            get_batches_directory_path(),
            _("batch metadata"),
            False,
        ),
        (
            expected_state.get("tracked_repository_paths"),
            expected_state.get("repository_files"),
            get_git_directory_path(),
            _("repository metadata"),
            False,
        ),
    ):
        if isinstance(tracked_paths, list) and isinstance(expected_files, dict):
            conflict_paths = [
                path
                for path in tracked_paths
                if not (ignore_selected and path.startswith("selected/"))
            ]
            current_files = _undo_snapshots.filesystem_directory_state(
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


def detect_undo_conflicts(manifest: CheckpointState) -> list[str]:
    """Return current-state conflicts with a checkpoint after-image."""
    after = manifest.get("after")
    if not isinstance(after, dict):
        return [_("incomplete checkpoint")]
    return _detect_conflicts_against_state(after)


def detect_redo_conflicts(manifest: CheckpointState) -> list[str]:
    """Return current-state conflicts with a redo node before-image."""
    after_undo = manifest.get("after_undo")
    if not isinstance(after_undo, dict):
        return [_("incomplete checkpoint")]
    return _detect_conflicts_against_state(after_undo)


def redo_relevant_paths(manifest: CheckpointState) -> list[str]:
    """Return worktree paths owned by an undo/redo checkpoint."""
    paths: set[str] = set()
    paths.update(manifest.get("tracked_worktree_paths", []))
    for entry in manifest.get("worktree_paths", []):
        paths.add(entry["path"])
    after = manifest.get("after")
    if isinstance(after, dict):
        for entry in after.get("worktree_paths", []):
            paths.add(entry["path"])
    if not uses_explicit_worktree_scope(manifest):
        paths.update(_undo_worktree.changed_worktree_paths())
    return sorted(paths)


def redo_relevant_index_paths(manifest: CheckpointState) -> list[str]:
    """Return index paths owned by an undo/redo checkpoint."""
    paths = set(
        manifest.get(
            "tracked_index_paths",
            manifest.get("tracked_worktree_paths", []),
        )
    )
    for state in (manifest.get("after"), manifest.get("after_undo")):
        if isinstance(state, dict):
            paths.update(state.get("tracked_index_paths", []))
            paths.update(state.get("index_entries", {}))
    paths.update(manifest.get("index_entries", {}))
    return sorted(paths)


def redo_relevant_refs(manifest: CheckpointState) -> list[str]:
    """Return refs owned by an undo/redo checkpoint."""
    refs = set(manifest.get("tracked_refs", []))
    for state in (manifest.get("after"), manifest.get("after_undo")):
        if isinstance(state, dict):
            refs.update(state.get("tracked_refs", []))
    return sorted(refs)


def restore_index_state(state: CheckpointState) -> None:
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
            if isinstance(entry, dict) and isinstance(
                entry.get("path"),
                str,
            ):
                scoped_paths.add(entry["path"])
    scoped_paths.update(index_entries)

    updates: list[GitIndexEntryUpdate] = []
    for file_path in sorted(scoped_paths):
        index_entry = index_entries.get(file_path)
        if isinstance(index_entry, dict):
            mode = index_entry.get("mode")
            object_id = index_entry.get("object_id")
            if isinstance(mode, str) and isinstance(object_id, str):
                updates.append(
                    GitIndexEntryUpdate(
                        file_path=file_path,
                        mode=mode,
                        blob_sha=object_id,
                    )
                )
                continue
        updates.append(
            GitIndexEntryUpdate(
                file_path=file_path,
                force_remove=True,
            )
        )
    git_update_index_entries(updates)


def restore_metadata_state(
    commit: str,
    manifest: CheckpointState,
) -> None:
    """Restore scoped application state with legacy whole-directory support."""
    for prefix, target_dir, tracked_paths, filesystem_state in (
        (
            "session",
            get_session_directory_path(),
            manifest.get("tracked_session_paths"),
            manifest.get("session_files"),
        ),
        (
            "batches",
            get_batches_directory_path(),
            manifest.get("tracked_batches_paths"),
            manifest.get("batches_files"),
        ),
    ):
        if isinstance(tracked_paths, list):
            _undo_restore.restore_tree_paths(
                commit,
                prefix=prefix,
                target_dir=target_dir,
                tracked_paths=tracked_paths,
                filesystem_state=filesystem_state,
            )
        else:
            _undo_restore.restore_tree_prefix(
                commit,
                prefix=prefix,
                target_dir=target_dir,
                filesystem_state=filesystem_state,
            )
    repository_paths = manifest.get("tracked_repository_paths")
    if isinstance(repository_paths, list):
        _undo_restore.restore_tree_paths(
            commit,
            prefix="repository",
            target_dir=get_git_directory_path(),
            tracked_paths=repository_paths,
            filesystem_state=manifest.get("repository_files"),
        )


def restore_checkpoint_state(
    commit: str,
    state: CheckpointState,
) -> None:
    """Restore all checkpoint-managed state from one snapshot commit."""
    restore_metadata_state(commit, state)
    _undo_restore.restore_refs(
        state.get("refs", {}),
        tracked_refs=state.get("tracked_refs"),
    )
    restore_index_state(state)
    _undo_restore.restore_worktree(commit, state)
    restore_intent_to_add_state(state)
