"""Git index updates from generated content buffers."""

from __future__ import annotations

from ..core.buffer import (
    LineBuffer,
    buffer_byte_count,
    buffer_preview,
)
from ..utils.git_command import run_git_command
from ..utils.git_index import git_update_index
from ..utils.git_object_io import create_git_blob
from ..utils.journal import log_journal


def update_index_with_blob_buffer(path: str, buffer: LineBuffer) -> None:
    """
    Update the git index with a new buffer for a file.

    Creates a temporary blob, hashes it, and updates the index entry.
    Preserves the file mode from the existing index entry if available.
    """
    ls_before = run_git_command(
        ["ls-files", "--stage", "--", path],
        check=False,
        requires_index_lock=False,
    ).stdout.strip()

    blob_hash = create_git_blob(buffer.byte_chunks())

    file_mode = ""
    try:
        ls_output = run_git_command(
            ["ls-files", "-s", "--", path],
            check=False,
            requires_index_lock=False,
        ).stdout.strip()
        if ls_output:
            file_mode = ls_output.split()[0]
    except Exception:
        file_mode = ""

    if not file_mode:
        file_mode = "100644"

    git_update_index(mode=file_mode, blob_sha=blob_hash, file_path=path)

    ls_after = run_git_command(
        ["ls-files", "--stage", "--", path],
        check=False,
        requires_index_lock=False,
    ).stdout.strip()
    log_journal(
        "update_index_with_blob_buffer",
        path=path,
        content_len=buffer_byte_count(buffer),
        buffer_preview=(
            buffer_preview(buffer).decode("utf-8", errors="replace")
            if buffer_byte_count(buffer) > 0 else
            "(empty)"
        ),
        blob_hash=blob_hash,
        file_mode=file_mode,
        index_before=ls_before,
        index_after=ls_after,
    )
