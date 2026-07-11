"""Snapshot persistence for the currently selected file."""

from __future__ import annotations

from contextlib import ExitStack

from ...core.buffer import (
    LineBuffer,
    buffer_matches,
    buffer_byte_count,
    buffer_preview,
    write_buffer_to_path,
)
from ...utils.repository_buffers import load_git_object_as_buffer
from ...utils.git_repository import get_git_repository_root_path
from ...utils.journal import JournalLevel, journal_enabled, log_journal
from ...utils.paths import get_index_snapshot_file_path, get_working_tree_snapshot_file_path


def write_snapshots_for_selected_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    with ExitStack() as stack:
        index_version = load_git_object_as_buffer(f":{file_path}")
        if index_version is None:
            index_version = LineBuffer.from_bytes(b"")
        stack.enter_context(index_version)

        repo_root = get_git_repository_root_path()
        file_full_path = repo_root / file_path
        if file_full_path.exists():
            working_tree_version = LineBuffer.from_path(file_full_path)
        else:
            working_tree_version = LineBuffer.from_bytes(b"")
        stack.enter_context(working_tree_version)

        # When index is empty but working tree has content, check if file exists in HEAD.
        # For new files (not in HEAD), use empty index snapshot.
        # For existing files with intent-to-add applied, use HEAD content.
        if buffer_byte_count(index_version) == 0 and buffer_byte_count(working_tree_version) > 0:
            head_version = load_git_object_as_buffer(f"HEAD:{file_path}")
            if head_version is not None:
                if buffer_byte_count(head_version) > 0:
                    index_version = stack.enter_context(head_version)
                else:
                    head_version.close()

        write_buffer_to_path(get_index_snapshot_file_path(), index_version)
        write_buffer_to_path(get_working_tree_snapshot_file_path(), working_tree_version)

        if journal_enabled():
            fields = {
                "file_path": file_path,
                "index_len": buffer_byte_count(index_version),
                "working_tree_len": buffer_byte_count(working_tree_version),
            }
            if journal_enabled(JournalLevel.CONTENT_DEBUG):
                fields.update({
                    "index_lines": _buffer_line_count(index_version),
                    "index_preview": buffer_preview(index_version),
                    "working_tree_lines": _buffer_line_count(working_tree_version),
                })
            log_journal("write_snapshots_for_selected_file", **fields)


def _buffer_line_count(buffer: LineBuffer) -> int:
    """Return a line count for journal metadata without materializing content."""
    line_breaks = 0
    seen_data = False
    pending_cr = False
    last_byte: int | None = None

    for chunk in buffer.byte_chunks():
        if not chunk:
            continue

        seen_data = True
        chunk_breaks = chunk.count(b"\n") + chunk.count(b"\r") - chunk.count(b"\r\n")
        if pending_cr and chunk.startswith(b"\n"):
            chunk_breaks -= 1

        line_breaks += chunk_breaks
        pending_cr = chunk.endswith(b"\r")
        last_byte = chunk[-1]

    if not seen_data:
        return 0
    if last_byte in (ord("\n"), ord("\r")):
        return line_breaks
    return line_breaks + 1


def snapshots_are_stale(file_path: str) -> bool:
    """Check if cached snapshots are stale (file changed since snapshots taken).

    Args:
        file_path: Repository-relative path to check

    Returns:
        True if the file has been committed or otherwise changed such that
        the cached hunk no longer applies
    """
    snapshot_base_path = get_index_snapshot_file_path()
    snapshot_new_path = get_working_tree_snapshot_file_path()

    # Missing snapshots means state is incomplete/stale
    if not snapshot_base_path.exists() or not snapshot_new_path.exists():
        return True

    try:
        with ExitStack() as stack:
            cached_index_content = stack.enter_context(
                LineBuffer.from_path(snapshot_base_path)
            )
            cached_worktree_content = stack.enter_context(
                LineBuffer.from_path(snapshot_new_path)
            )

            selected_index_content = load_git_object_as_buffer(f":{file_path}")
            if selected_index_content is None:
                selected_index_content = LineBuffer.from_bytes(b"")
            stack.enter_context(selected_index_content)

            repo_root = get_git_repository_root_path()
            file_full_path = repo_root / file_path
            if file_full_path.exists():
                selected_worktree_content = LineBuffer.from_path(file_full_path)
            else:
                selected_worktree_content = LineBuffer.from_bytes(b"")
            stack.enter_context(selected_worktree_content)

            return (
                not buffer_matches(cached_index_content, selected_index_content)
                or not buffer_matches(cached_worktree_content, selected_worktree_content)
            )
    except Exception:
        return True  # Error reading means state is stale
