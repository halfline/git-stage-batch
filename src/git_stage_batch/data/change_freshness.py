"""Freshness checks for cached live file changes."""

from __future__ import annotations

from ..batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ..core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    RenameChange,
    TextFileDeletionChange,
)
from .text_lifecycle_detection import detect_empty_text_lifecycle_change
from .file_change_display import (
    render_binary_file_change,
    render_gitlink_change,
    render_mode_change,
    render_rename_change,
    render_text_deletion_change,
)
from .selected_change.snapshots import snapshots_are_stale


def binary_file_change_is_stale(
    binary_change: BinaryFileChange,
    *,
    comparison_base: str | None = None,
) -> bool:
    """Return whether a cached binary selection no longer matches repository state."""
    file_path = binary_change.path()
    if snapshots_are_stale(file_path):
        return True
    current_change = render_binary_file_change(
        file_path,
        base=comparison_base,
    )
    if current_change is None:
        return True
    return (
        current_change.old_path != binary_change.old_path
        or current_change.new_path != binary_change.new_path
        or current_change.change_type != binary_change.change_type
        or current_change.content_fingerprint != binary_change.content_fingerprint
    )


def file_mode_change_is_stale(mode_change: FileModeChange) -> bool:
    """Return whether a cached executable-mode action changed."""
    return render_mode_change(mode_change.path()) != mode_change


def gitlink_change_is_stale(
    gitlink_change: GitlinkChange,
    *,
    comparison_base: str | None = None,
) -> bool:
    """Return whether a cached gitlink selection no longer matches Git state."""
    current_change = render_gitlink_change(
        gitlink_change.path(),
        base=comparison_base,
    )
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
    *,
    batch_metadata_by_name: dict[str, dict] | None = None,
) -> bool:
    """Return whether a whole-text-file deletion is already represented in a batch."""
    return empty_text_lifecycle_change_is_batched(
        deletion_change.path(),
        batch_metadata_by_name=batch_metadata_by_name,
    )


def empty_text_lifecycle_change_is_batched(
    file_path: str,
    *,
    batch_metadata_by_name: dict[str, dict] | None = None,
) -> bool:
    """Return whether the current empty text lifecycle diff is already batched."""
    change_type = detect_empty_text_lifecycle_change(file_path)
    if change_type is None:
        return False

    if batch_metadata_by_name is None:
        batch_metadata_by_name = read_batch_metadata_for_batches(list_batch_names())

    for metadata in batch_metadata_by_name.values():
        file_meta = metadata.get("files", {}).get(file_path)
        if file_meta is not None and file_meta.get("change_type") == change_type:
            return True
    return False
