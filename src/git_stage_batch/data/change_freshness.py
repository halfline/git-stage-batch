"""Freshness checks for cached live file changes."""

from __future__ import annotations

from ..batch.query import list_batch_names, read_batch_metadata
from ..core.models import (
    BinaryFileChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from .text_lifecycle_detection import detect_empty_text_lifecycle_change
from .file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_rename_change,
    render_text_deletion_change,
)
from .selected_change.snapshots import snapshots_are_stale


def binary_file_change_is_stale(binary_change: BinaryFileChange) -> bool:
    """Return whether a cached binary selection no longer matches repository state."""
    file_path = (
        binary_change.new_path
        if binary_change.new_path != "/dev/null"
        else binary_change.old_path
    )
    if snapshots_are_stale(file_path):
        return True
    current_change = render_binary_file_change(file_path)
    if current_change is None:
        return True
    return (
        current_change.old_path != binary_change.old_path
        or current_change.new_path != binary_change.new_path
        or current_change.change_type != binary_change.change_type
    )


def gitlink_change_is_stale(gitlink_change: GitlinkChange) -> bool:
    """Return whether a cached gitlink selection no longer matches Git state."""
    current_change = render_gitlink_change(gitlink_change.path())
    if current_change is None:
        return True
    return (
        current_change.old_path != gitlink_change.old_path
        or current_change.new_path != gitlink_change.new_path
        or current_change.old_oid != gitlink_change.old_oid
        or current_change.new_oid != gitlink_change.new_oid
        or current_change.change_type != gitlink_change.change_type
    )


def rename_change_is_stale(rename_change: RenameChange) -> bool:
    """Return whether a cached rename selection no longer matches Git state."""
    current_change = render_rename_change(rename_change.new_path)
    if current_change is None:
        current_change = render_rename_change(rename_change.old_path)
    if current_change is None:
        return True
    return (
        current_change.old_path != rename_change.old_path
        or current_change.new_path != rename_change.new_path
    )


def text_deletion_change_is_stale(
    deletion_change: TextFileDeletionChange,
) -> bool:
    """Return whether a cached text deletion selection no longer matches Git state."""
    if snapshots_are_stale(deletion_change.path()):
        return True
    current_change = render_text_deletion_change(deletion_change.path())
    if current_change is None:
        return True
    return (
        current_change.old_path != deletion_change.old_path
        or current_change.new_path != deletion_change.new_path
    )


def text_deletion_change_is_batched(
    deletion_change: TextFileDeletionChange,
) -> bool:
    """Return whether a whole-text-file deletion is already represented in a batch."""
    return empty_text_lifecycle_change_is_batched(deletion_change.path())


def empty_text_lifecycle_change_is_batched(file_path: str) -> bool:
    """Return whether the current empty text lifecycle diff is already batched."""
    change_type = detect_empty_text_lifecycle_change(file_path)
    if change_type is None:
        return False

    for batch_name in list_batch_names():
        file_meta = read_batch_metadata(batch_name).get("files", {}).get(file_path)
        if file_meta is not None and file_meta.get("change_type") == change_type:
            return True
    return False
