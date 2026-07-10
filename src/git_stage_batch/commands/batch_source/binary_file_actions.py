"""Binary file actions for batch-source commands."""

from __future__ import annotations

from enum import Enum
import os

from ...core.buffer import (
    LineBuffer,
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)
from ...data.file_modes import apply_git_file_mode, detect_file_mode_in_commit
from ...utils.repository_buffers import load_git_object_as_buffer
from ...utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    git_update_index,
)


class BinaryWorktreeAction(Enum):
    """Result of a binary batch target written into the working tree."""

    ADDED = "added"
    DELETED = "deleted"
    REPLACED = "replaced"


def discard_binary_file_to_worktree(
    file_path: str,
    baseline_commit: str,
) -> BinaryWorktreeAction | None:
    """Restore or remove one binary file using the batch baseline commit."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    baseline_buffer = load_git_object_as_buffer(f"{baseline_commit}:{file_path}")
    if baseline_buffer is not None:
        with baseline_buffer:
            write_buffer_to_path(full_path, baseline_buffer)
        apply_git_file_mode(
            full_path,
            detect_file_mode_in_commit(baseline_commit, file_path),
        )
        return BinaryWorktreeAction.REPLACED

    if full_path.exists():
        full_path.unlink()
        return BinaryWorktreeAction.DELETED
    return None


def write_binary_file_to_worktree(
    file_path: str,
    file_meta: dict,
    buffer: LineBuffer | None,
    *,
    missing_content_message: str | None = None,
) -> BinaryWorktreeAction | None:
    """Write one binary batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    change_type = file_meta.get("change_type", "modified")

    if change_type == "deleted":
        if os.path.lexists(full_path):
            full_path.unlink()
            return BinaryWorktreeAction.DELETED
        return None

    if buffer is None:
        if missing_content_message is None:
            missing_content_message = (
                f"Binary file not found in batch commit: {file_path}"
            )
        raise RuntimeError(missing_content_message)

    write_buffer_to_working_tree_path(
        full_path,
        buffer,
        mode=str(file_meta.get("mode", "100644")),
    )

    if change_type == "added":
        return BinaryWorktreeAction.ADDED
    return BinaryWorktreeAction.REPLACED


def stage_binary_file_to_index(
    file_path: str,
    file_meta: dict,
    buffer: LineBuffer | None,
) -> None:
    """Stage one binary batch target into the index."""
    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        result = git_update_index(file_path=file_path, force_remove=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to stage binary deletion for {file_path}: {result.stderr}"
            )
        return

    if buffer is None:
        raise RuntimeError(f"Binary file not found in batch commit: {file_path}")

    blob_hash = create_git_blob(buffer.byte_chunks())
    file_mode = file_meta.get("mode", "100644")
    git_update_index(file_path=file_path, mode=str(file_mode), blob_sha=blob_hash)
