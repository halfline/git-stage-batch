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


_OPTIONAL_STRING_FIELDS = frozenset({"head"})
_STRING_FIELDS = frozenset({
    "index_tree",
    "operation",
    "undo_checkpoint",
})
_STRING_LIST_FIELDS = frozenset({
    "intent_to_add_paths",
    "tracked_worktree_paths",
    "tracked_index_paths",
    "tracked_refs",
    "tracked_session_paths",
    "tracked_batches_paths",
    "tracked_repository_paths",
})
_STRING_MAPPING_FIELDS = frozenset({"refs", "recovery_anchors"})
_FILESYSTEM_FIELDS = frozenset({
    "session_files",
    "batches_files",
    "repository_files",
})
_WORKTREE_OPTIONAL_STRING_FIELDS = frozenset({
    "mode",
    "storage_mode",
})
_WORKTREE_NULLABLE_STRING_FIELDS = frozenset({
    "blob",
    "index_oid",
    "head_oid",
    "worktree_oid",
})
_WORKTREE_BOOLEAN_FIELDS = frozenset({"exists", "dirty", "archive"})


def _string_mapping(value: object) -> TypeGuard[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    )


def _string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(
        isinstance(item, str) for item in value
    )


def _object_entry_mapping(
    value: object,
) -> TypeGuard[dict[str, IndexEntryState]]:
    if not isinstance(value, dict):
        return False
    for path, entry in value.items():
        if not isinstance(path, str) or not isinstance(entry, dict):
            return False
        if not isinstance(entry.get("mode"), str):
            return False
        if not isinstance(entry.get("object_id"), str):
            return False
    return True


def _filesystem_state(value: object) -> TypeGuard[FilesystemState]:
    return _object_entry_mapping(value)


def _worktree_path_state(value: object) -> TypeGuard[WorktreePathState]:
    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
        return False
    kind = value.get("kind")
    if kind is not None and kind not in {"gitlink", "embedded-repo"}:
        return False
    for field in _WORKTREE_OPTIONAL_STRING_FIELDS:
        field_value = value.get(field)
        if field_value is not None and not isinstance(field_value, str):
            return False
    for field in _WORKTREE_NULLABLE_STRING_FIELDS:
        if field in value:
            field_value = value[field]
            if field_value is not None and not isinstance(field_value, str):
                return False
    for field in _WORKTREE_BOOLEAN_FIELDS:
        if field in value and type(value[field]) is not bool:
            return False
    return True


def _worktree_path_states(
    value: object,
) -> TypeGuard[list[WorktreePathState]]:
    return isinstance(value, list) and all(
        _worktree_path_state(item) for item in value
    )


def is_checkpoint_state(value: object) -> TypeGuard[CheckpointState]:
    """Return whether a decoded JSON value has the checkpoint field shapes."""
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        return False
    values = cast(dict[str, object], value)
    for field in _OPTIONAL_STRING_FIELDS:
        if field in values:
            field_value = values[field]
            if field_value is not None and not isinstance(field_value, str):
                return False
    for field in _STRING_FIELDS:
        if field in values and not isinstance(values[field], str):
            return False
    for field in _STRING_LIST_FIELDS:
        if field in values and not _string_list(values[field]):
            return False
    for field in _STRING_MAPPING_FIELDS:
        if field in values and not _string_mapping(values[field]):
            return False
    if (
        "index_entries" in values
        and not _object_entry_mapping(values["index_entries"])
    ):
        return False
    if (
        "worktree_paths" in values
        and not _worktree_path_states(values["worktree_paths"])
    ):
        return False
    for field in _FILESYSTEM_FIELDS:
        if field in values and not _filesystem_state(values[field]):
            return False
    scope = values.get("worktree_path_scope")
    if scope is not None and scope != "explicit":
        return False
    for field in ("after", "after_undo"):
        if field in values and not is_checkpoint_state(values[field]):
            return False
    return True


def worktree_metadata_without_blob(
    entry: WorktreePathState,
) -> WorktreePathState:
    """Copy one worktree record without its separately stored blob identity."""
    metadata = cast(WorktreePathState, dict(entry))
    metadata.pop("blob", None)
    return metadata
