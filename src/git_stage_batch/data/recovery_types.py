"""Concrete persisted mappings used by session recovery checkpoints."""

from __future__ import annotations

from typing import Literal, TypeAlias, TypeGuard, TypedDict, cast


class IndexEntryState(TypedDict):
    """One exact index entry saved in a checkpoint."""

    mode: str
    object_id: str


class FilesystemEntryState(TypedDict):
    """One application-state file saved below a checkpoint tree prefix."""

    mode: str
    object_id: str


class _RequiredWorktreePathState(TypedDict):
    """Fields required to identify one checkpointed worktree path."""

    path: str


class WorktreePathState(_RequiredWorktreePathState, total=False):
    """Current and legacy before-image data for one worktree path."""

    exists: bool
    mode: str
    kind: Literal["gitlink", "embedded-repo"]
    blob: str | None
    index_oid: str | None
    head_oid: str | None
    worktree_oid: str | None
    dirty: bool
    archive: bool
    storage_mode: str


FilesystemState: TypeAlias = dict[str, FilesystemEntryState]


class CheckpointState(TypedDict, total=False):
    """One compatible undo/redo state or manifest mapping."""

    head: str | None
    index_tree: str
    operation: str
    undo_checkpoint: str
    index_entries: dict[str, IndexEntryState]
    refs: dict[str, str]
    recovery_anchors: dict[str, str]
    intent_to_add_paths: list[str]
    tracked_worktree_paths: list[str]
    tracked_index_paths: list[str]
    tracked_refs: list[str]
    tracked_session_paths: list[str]
    tracked_batches_paths: list[str]
    tracked_repository_paths: list[str]
    worktree_paths: list[WorktreePathState]
    session_files: FilesystemState
    batches_files: FilesystemState
    repository_files: FilesystemState
    worktree_path_scope: Literal["explicit"]
    after: CheckpointState
    after_undo: CheckpointState


















def worktree_metadata_without_blob(
    entry: WorktreePathState,
) -> WorktreePathState:
    """Copy one worktree record without its separately stored blob identity."""
    metadata = cast(WorktreePathState, dict(entry))
    metadata.pop("blob", None)
    return metadata
