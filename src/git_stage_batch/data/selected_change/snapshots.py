"""Snapshot persistence for the currently selected file."""

from __future__ import annotations

from contextlib import ExitStack
import json

from ...core.buffer import (
    LineBuffer,
    buffer_matches,
    buffer_byte_count,
    buffer_preview,
    write_buffer_to_path,
)
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    read_working_tree_object,
)
from ...exceptions import RepositoryPathMissing
from ...data.index_entries import read_index_entry
from ...data.session import path_is_intent_to_add
from ...utils.file_io import write_text_file_contents
from ...utils.journal import JournalLevel, journal_enabled, log_journal
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_snapshot_metadata_file_path,
    get_working_tree_snapshot_file_path,
)


_SNAPSHOT_SCHEMA_VERSION = 2


def write_snapshots_for_selected_file_path(file_path: str) -> None:
    """Write snapshots of the file from both the index and working tree."""
    with ExitStack() as stack:
        index_entry = read_index_entry(file_path)
        index_version = read_git_object_buffer_or_none(f":{file_path}")
        if index_version is None:
            index_version = LineBuffer.from_bytes(b"")
        stack.enter_context(index_version)

        try:
            worktree_object = read_working_tree_object(file_path)
            working_tree_version = worktree_object.buffer
            worktree_metadata = {
                "exists": True,
                "kind": worktree_object.kind,
                "mode": worktree_object.git_mode,
            }
        except RepositoryPathMissing:
            working_tree_version = LineBuffer.from_bytes(b"")
            worktree_metadata = {"exists": False, "kind": None, "mode": None}
        stack.enter_context(working_tree_version)
        index_snapshot_source = "index"
        index_is_intent_to_add = (
            index_entry is not None and path_is_intent_to_add(file_path)
        )

        # When index is empty but working tree has content, check if file exists in HEAD.
        # For new files (not in HEAD), use empty index snapshot.
        # For existing files with intent-to-add applied, use HEAD content.
        if (
            buffer_byte_count(index_version) == 0
            and buffer_byte_count(working_tree_version) > 0
            and index_is_intent_to_add
        ):
            head_version = read_git_object_buffer_or_none(f"HEAD:{file_path}")
            if head_version is not None:
                if buffer_byte_count(head_version) > 0:
                    index_version = stack.enter_context(head_version)
                    index_snapshot_source = "head"
                else:
                    head_version.close()

        write_buffer_to_path(get_index_snapshot_file_path(), index_version)
        write_buffer_to_path(
            get_working_tree_snapshot_file_path(), working_tree_version
        )
        manifest = {
            "schema_version": _SNAPSHOT_SCHEMA_VERSION,
            "path": file_path,
            "index": {
                "exists": index_entry is not None,
                "mode": index_entry.mode if index_entry is not None else None,
                "object_id": (
                    index_entry.object_id if index_entry is not None else None
                ),
                "intent_to_add": index_is_intent_to_add,
                "snapshot_source": index_snapshot_source,
            },
            "worktree": worktree_metadata,
        }
        write_text_file_contents(
            get_snapshot_metadata_file_path(),
            json.dumps(manifest, sort_keys=True) + "\n",
        )

        if journal_enabled():
            fields = {
                "file_path": file_path,
                "index_len": buffer_byte_count(index_version),
                "working_tree_len": buffer_byte_count(working_tree_version),
            }
            if journal_enabled(JournalLevel.CONTENT_DEBUG):
                fields.update(
                    {
                        "index_lines": _buffer_line_count(index_version),
                        "index_preview": buffer_preview(index_version),
                        "working_tree_lines": _buffer_line_count(working_tree_version),
                    }
                )
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
    metadata_path = get_snapshot_metadata_file_path()

    # Missing snapshots means state is incomplete/stale
    if (
        not snapshot_base_path.exists()
        or not snapshot_new_path.exists()
        or not metadata_path.exists()
    ):
        return True

    try:
        with ExitStack() as stack:
            cached_index_content = stack.enter_context(
                LineBuffer.from_path(snapshot_base_path)
            )
            cached_worktree_content = stack.enter_context(
                LineBuffer.from_path(snapshot_new_path)
            )
            manifest = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                manifest.get("schema_version") != _SNAPSHOT_SCHEMA_VERSION
                or manifest.get("path") != file_path
            ):
                return True

            index_entry = read_index_entry(file_path)
            index_metadata = {
                "exists": index_entry is not None,
                "mode": index_entry.mode if index_entry is not None else None,
                "object_id": (
                    index_entry.object_id if index_entry is not None else None
                ),
                "intent_to_add": (
                    index_entry is not None and path_is_intent_to_add(file_path)
                ),
                "snapshot_source": manifest.get("index", {}).get(
                    "snapshot_source"
                ),
            }
            if manifest.get("index") != index_metadata:
                return True
            selected_index_content = read_git_object_buffer_or_none(f":{file_path}")
            if selected_index_content is None:
                selected_index_content = LineBuffer.from_bytes(b"")
            stack.enter_context(selected_index_content)
            index_snapshot_source = manifest["index"]["snapshot_source"]
            if index_snapshot_source == "head":
                selected_snapshot_base = read_git_object_buffer_or_none(
                    f"HEAD:{file_path}"
                )
                if selected_snapshot_base is None:
                    return True
                stack.enter_context(selected_snapshot_base)
            elif index_snapshot_source == "index":
                selected_snapshot_base = selected_index_content
            else:
                return True

            try:
                worktree_object = read_working_tree_object(file_path)
                selected_worktree_content = worktree_object.buffer
                worktree_metadata = {
                    "exists": True,
                    "kind": worktree_object.kind,
                    "mode": worktree_object.git_mode,
                }
            except RepositoryPathMissing:
                selected_worktree_content = LineBuffer.from_bytes(b"")
                worktree_metadata = {"exists": False, "kind": None, "mode": None}
            stack.enter_context(selected_worktree_content)
            if manifest.get("worktree") != worktree_metadata:
                return True

            return not buffer_matches(
                cached_index_content, selected_snapshot_base
            ) or not buffer_matches(cached_worktree_content, selected_worktree_content)
    except Exception:
        return True  # Error reading means state is stale
