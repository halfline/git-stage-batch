"""Publish reconstructed worktree content for line discard actions."""

from __future__ import annotations

from pathlib import Path

from ...core.buffer import (
    BufferInput,
    buffer_matches,
    write_buffer_to_working_tree_path,
)
from ...data.file_modes import detect_file_mode
from ...data.index_entries import read_index_entry
from ...utils.repository_buffers import read_git_object_buffer_or_none


def publish_worktree_line_discard(
    file_path: str,
    absolute_path: Path,
    buffer: BufferInput,
    *,
    force_regular: bool = False,
) -> None:
    """Publish reconstructed line content with an explicit path-kind policy."""
    mode = None if force_regular else _line_discard_publication_mode(file_path, buffer)
    write_buffer_to_working_tree_path(absolute_path, buffer, mode=mode)


def _line_discard_publication_mode(
    file_path: str,
    buffer: BufferInput,
) -> str | None:
    """Return an explicit mode only when publication must change path kind."""
    worktree_mode = detect_file_mode(file_path)
    index_entry = read_index_entry(file_path)

    if worktree_mode == "120000":
        if index_entry is not None and index_entry.mode != "120000":
            return index_entry.mode
        return "120000"

    if index_entry is None or index_entry.mode != "120000":
        return None

    index_buffer = read_git_object_buffer_or_none(f":{file_path}")
    if index_buffer is None:
        return None
    with index_buffer:
        if buffer_matches(buffer, index_buffer):
            return "120000"
    return None
