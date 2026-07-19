"""Repository-backed line buffer loading helpers."""

from __future__ import annotations

import os
import errno
import stat
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..core.buffer import LineBuffer
from ..utils.git_command import stream_git_command
from ..utils.git_command import run_git_command
from ..utils.git_repository import get_git_repository_root_path
from ..utils.git_object_io import (
    list_git_tree_blobs,
    read_git_blob,
    stream_git_blobs,
    resolve_git_objects,
)
from ..exceptions import (
    GitOperationFailed,
    RepositoryDataInvalid,
    RepositoryObjectMissing,
    RepositoryPathInaccessible,
    RepositoryPathMissing,
)


@dataclass(frozen=True)
class GitBlobBuffer:
    """One streamed Git blob exposed as a bounded line buffer."""

    requested_name: str
    object_id: str
    size: int
    buffer: LineBuffer


@dataclass(frozen=True)
class WorkingTreeObject:
    """Content and Git-visible metadata for one worktree directory entry."""

    buffer: LineBuffer
    kind: str
    git_mode: str


def load_git_blob_as_buffer(
    blob_sha: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Load a Git blob as a line buffer."""
    return LineBuffer.from_chunks(
        read_git_blob(blob_sha),
        spool_dir=spool_dir,
    )


def stream_git_blob_buffers(
    blob_names: Iterable[str],
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[GitBlobBuffer]:
    """Yield one mmap-capable line buffer at a time from a Git batch reader."""
    for blob in stream_git_blobs(blob_names):
        buffer = LineBuffer.from_chunks(
            blob.content_chunks,
            spool_dir=spool_dir,
        )
        try:
            yield GitBlobBuffer(
                requested_name=blob.requested_name,
                object_id=blob.object_id,
                size=blob.size,
                buffer=buffer,
            )
        finally:
            buffer.close()


def load_git_tree_files_as_buffers(
    treeish: str,
    file_paths: list[str],
    *,
    spool_dir: str | Path | None = None,
) -> dict[str, LineBuffer]:
    """Load files from a Git tree as line buffers."""
    tree_blobs = list_git_tree_blobs(treeish, file_paths)
    buffers: dict[str, LineBuffer] = {}
    try:
        for file_path, blob in tree_blobs.items():
            buffers[file_path] = load_git_blob_as_buffer(
                blob.blob_sha,
                spool_dir=spool_dir,
            )
    except BaseException:
        for buffer in buffers.values():
            buffer.close()
        raise
    return buffers


def read_git_object_buffer_or_none(
    revision_path: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer | None:
    """Load a Git object, returning ``None`` only when it is absent."""
    # cat-file's traditional batch protocol is line-delimited and cannot
    # represent object expressions containing newlines. Validate the revision
    # independently, then use argv-safe `git show` for these unusual paths.
    try:
        revision_path.encode("utf-8")
        batch_protocol_safe = "\n" not in revision_path and "\r" not in revision_path
    except UnicodeEncodeError:
        batch_protocol_safe = False
    if not batch_protocol_safe:
        revision = (
            revision_path[1:]
            if revision_path.startswith(":")
            else revision_path.split(":", 1)[0]
        )
        if not revision_path.startswith(":"):
            try:
                run_git_command(
                    ["rev-parse", "--verify", f"{revision}^{{tree}}"],
                    requires_index_lock=False,
                )
            except subprocess.CalledProcessError as error:
                raise GitOperationFailed(
                    f"Could not resolve Git revision {revision!r}"
                ) from error
        try:
            return LineBuffer.from_chunks(
                _stream_git_object(revision_path),
                spool_dir=spool_dir,
            )
        except subprocess.CalledProcessError:
            return None
    try:
        resolved = resolve_git_objects([revision_path]).get(revision_path)
    except (subprocess.CalledProcessError, RuntimeError) as error:
        raise GitOperationFailed(
            f"Could not resolve Git object {revision_path!r}"
        ) from error
    if resolved is None:
        return None
    if resolved.object_type != "blob":
        raise RepositoryDataInvalid(
            f"Git object {revision_path!r} is {resolved.object_type}, not a blob"
        )
    try:
        return load_git_blob_as_buffer(
            resolved.object_id,
            spool_dir=spool_dir,
        )
    except (subprocess.CalledProcessError, RuntimeError) as error:
        raise GitOperationFailed(
            f"Could not read Git object {revision_path!r}"
        ) from error


def read_git_object_buffer(
    revision_path: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Load a Git blob or raise a typed missing/failure exception."""
    buffer = read_git_object_buffer_or_none(
        revision_path,
        spool_dir=spool_dir,
    )
    if buffer is None:
        raise RepositoryObjectMissing(revision_path)
    return buffer


def read_git_object_buffer_or_empty(
    revision_path: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Load a Git object as a line buffer, or an empty buffer if missing."""
    buffer = read_git_object_buffer_or_none(
        revision_path,
        spool_dir=spool_dir,
    )
    if buffer is None:
        return LineBuffer.from_bytes(b"", spool_dir=spool_dir)
    return buffer


def load_working_tree_file_as_buffer(
    file_path: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Load a working-tree file, returning empty only when it is absent."""
    try:
        return read_working_tree_object(
            file_path,
            spool_dir=spool_dir,
        ).buffer
    except RepositoryPathMissing:
        return LineBuffer.from_bytes(b"", spool_dir=spool_dir)


def read_working_tree_object(
    file_path: str,
    *,
    spool_dir: str | Path | None = None,
) -> WorkingTreeObject:
    """Read Git-visible worktree content without following symlinks."""
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    try:
        metadata = full_path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            return WorkingTreeObject(
                buffer=LineBuffer.from_bytes(
                    os.readlink(os.fsencode(full_path)),
                    spool_dir=spool_dir,
                ),
                kind="symlink",
                git_mode="120000",
            )
        if not stat.S_ISREG(metadata.st_mode):
            raise RepositoryDataInvalid(
                f"Unsupported working-tree path kind: {file_path}"
            )
        mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
        return WorkingTreeObject(
            buffer=LineBuffer.from_path(
                full_path,
                spool_dir=spool_dir,
            ),
            kind="regular",
            git_mode=mode,
        )
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR):
            raise RepositoryPathMissing(file_path) from error
        raise RepositoryPathInaccessible(
            f"Could not read working-tree path {file_path!r}"
        ) from error


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
