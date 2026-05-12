"""Git loading helpers for editor buffers."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

from .buffer import EditorBuffer
from ..utils.command import ExitEvent, OutputEvent, stream_command
from ..utils.git import (
    get_git_repository_root_path,
    list_git_tree_blobs,
    read_git_blob,
)


def load_git_blob_as_buffer(blob_sha: str) -> EditorBuffer:
    """Load a Git blob as an editor buffer."""
    return EditorBuffer.from_chunks(read_git_blob(blob_sha))


def load_git_tree_files_as_buffers(
    treeish: str,
    file_paths: list[str],
) -> dict[str, EditorBuffer]:
    """Load files from a Git tree as editor buffers."""
    tree_blobs = list_git_tree_blobs(treeish, file_paths)
    buffers: dict[str, EditorBuffer] = {}
    try:
        for file_path, blob in tree_blobs.items():
            buffers[file_path] = load_git_blob_as_buffer(blob.blob_sha)
    except Exception:
        for buffer in buffers.values():
            buffer.close()
        raise
    return buffers


def load_git_object_as_buffer(revision_path: str) -> EditorBuffer | None:
    """Load a Git object as an editor buffer."""
    try:
        return EditorBuffer.from_chunks(_stream_git_object(revision_path))
    except subprocess.CalledProcessError:
        return None


def load_git_object_as_buffer_or_empty(revision_path: str) -> EditorBuffer:
    """Load a Git object as an editor buffer, or an empty buffer if missing."""
    buffer = load_git_object_as_buffer(revision_path)
    if buffer is None:
        return EditorBuffer.from_bytes(b"")
    return buffer


def load_working_tree_file_as_buffer(file_path: str) -> EditorBuffer:
    """Load a working-tree file as an editor buffer."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    try:
        if full_path.is_symlink():
            return EditorBuffer.from_bytes(os.readlink(os.fsencode(full_path)))
        return EditorBuffer.from_path(full_path)
    except OSError:
        return EditorBuffer.from_bytes(b"")


def _stream_git_object(revision_path: str) -> Iterator[bytes]:
    stderr_chunks: list[bytes] = []
    exit_code = 0

    for event in stream_command(["git", "show", revision_path]):
        if isinstance(event, ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, OutputEvent):
            if event.fd == 1:
                yield event.data
            elif event.fd == 2:
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            exit_code,
            ["git", "show", revision_path],
            stderr=stderr_text,
        )
