"""Git index updates from generated content buffers."""

from __future__ import annotations

from ..core.buffer import (
    LineBuffer,
    buffer_byte_count,
    buffer_preview,
)
from ..data.index_entries import IndexEntry, read_index_entry
from ..utils.git_index import git_update_index
from ..utils.git_object_io import create_git_blob
from ..utils.journal import JournalLevel, journal_enabled, log_journal


def _index_entry_fields(entry: IndexEntry | None) -> dict[str, str] | None:
    """Convert an index entry to content-free journal fields."""
    if entry is None:
        return None
    return {"mode": entry.mode, "object_id": entry.object_id}


def update_index_with_blob_buffer(path: str, buffer: LineBuffer) -> None:
    """
    Update the git index with a new buffer for a file.

    Creates a temporary blob, hashes it, and updates the index entry.
    Preserves the file mode from the existing index entry if available.
    """
    index_before = read_index_entry(path)

    blob_hash = create_git_blob(buffer.byte_chunks())

    file_mode = index_before.mode if index_before is not None else "100644"

    git_update_index(mode=file_mode, blob_sha=blob_hash, file_path=path)

    if journal_enabled():
        fields = {
            "path": path,
            "content_len": buffer_byte_count(buffer),
            "blob_hash": blob_hash,
            "file_mode": file_mode,
            "index_entry_before": _index_entry_fields(index_before),
            "index_entry_after": _index_entry_fields(read_index_entry(path)),
        }
        if journal_enabled(JournalLevel.CONTENT_DEBUG):
            fields["buffer_preview"] = buffer_preview(buffer)
        log_journal("update_index_with_blob_buffer", **fields)
