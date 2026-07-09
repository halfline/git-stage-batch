"""Repository-backed line buffer loading helpers."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

from ..core.buffer import LineBuffer
from ..utils.git import (
    get_git_repository_root_path,
    stream_git_command,
)
from ..utils.git_object_io import list_git_tree_blobs, read_git_blob


def load_git_blob_as_buffer(blob_sha: str) -> LineBuffer:
    """Load a Git blob as a line buffer."""
    return LineBuffer.from_chunks(read_git_blob(blob_sha))


def load_git_tree_files_as_buffers(
    treeish: str,
    file_paths: list[str],
) -> dict[str, LineBuffer]:
    """Load files from a Git tree as line buffers."""
    tree_blobs = list_git_tree_blobs(treeish, file_paths)
    buffers: dict[str, LineBuffer] = {}
    try:
        for file_path, blob in tree_blobs.items():
            buffers[file_path] = load_git_blob_as_buffer(blob.blob_sha)
    except Exception:
        for buffer in buffers.values():
            buffer.close()
        raise
    return buffers


def load_git_object_as_buffer(revision_path: str) -> LineBuffer | None:
    """Load a Git object as a line buffer."""
    try:
        return LineBuffer.from_chunks(_stream_git_object(revision_path))
    except subprocess.CalledProcessError:
        return None


def load_git_object_as_buffer_or_empty(revision_path: str) -> LineBuffer:
    """Load a Git object as a line buffer, or an empty buffer if missing."""
    buffer = load_git_object_as_buffer(revision_path)
    if buffer is None:
        return LineBuffer.from_bytes(b"")
    return buffer


def load_working_tree_file_as_buffer(file_path: str) -> LineBuffer:
    """Load a working-tree file as a line buffer."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    try:
        if full_path.is_symlink():
            return LineBuffer.from_bytes(os.readlink(os.fsencode(full_path)))
        return LineBuffer.from_path(full_path)
    except OSError:
        return LineBuffer.from_bytes(b"")


def _stream_git_object(revision_path: str) -> Iterator[bytes]:
    try:
        yield from stream_git_command(
            ["show", revision_path],
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError as error:
        raise subprocess.CalledProcessError(
            error.returncode,
            ["git", "show", revision_path],
            stderr=error.stderr,
        ) from error
