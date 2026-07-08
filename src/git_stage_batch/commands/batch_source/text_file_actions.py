"""Text file working-tree actions for batch-source commands."""

from __future__ import annotations

import os

from ...core.buffer import LineBuffer, write_buffer_to_working_tree_path
from ...core.text_lifecycle import TextFileChangeType, normalized_text_change_type
from ...utils.git import get_git_repository_root_path


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
