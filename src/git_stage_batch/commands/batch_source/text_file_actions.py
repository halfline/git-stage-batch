"""Text file working-tree actions for batch-source commands."""

from __future__ import annotations

import os

from ...core.buffer import (
    LineBuffer,
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)
from ...core.text_lifecycle import TextFileChangeType, normalized_text_change_type
from ...data.file_modes import apply_git_file_mode
from ...staging.operations import update_index_with_blob_buffer
from ...utils.git import git_update_index
from ...utils.git_repository import get_git_repository_root_path
from ...utils.git_object_io import create_git_blob


def stage_text_file_to_index(
    file_path: str,
    buffer: LineBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Stage one text batch target into the index."""
    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        result = git_update_index(file_path=file_path, force_remove=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to stage text deletion for {file_path}: {result.stderr}"
            )
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    if file_mode is None:
        update_index_with_blob_buffer(file_path, buffer)
        return

    blob_hash = create_git_blob(buffer.byte_chunks())
    git_update_index(file_path=file_path, mode=file_mode, blob_sha=blob_hash)


def write_text_file_to_worktree(
    file_path: str,
    buffer: LineBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Write one text batch target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        if os.path.lexists(full_path):
            full_path.unlink()
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in batch content: {file_path}")

    write_buffer_to_working_tree_path(full_path, buffer, mode=file_mode)


def write_discarded_text_file_to_worktree(
    file_path: str,
    buffer: LineBuffer | None,
    file_mode: str | None,
    change_type: str = "modified",
) -> None:
    """Write one discarded text target into the working tree."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path

    if normalized_text_change_type(change_type) == TextFileChangeType.DELETED:
        if full_path.exists():
            full_path.unlink()
        return

    if buffer is None:
        raise RuntimeError(f"Text file not found in discarded content: {file_path}")

    write_buffer_to_path(full_path, buffer)
    apply_git_file_mode(full_path, file_mode)
