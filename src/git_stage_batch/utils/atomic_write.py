"""Low-level durable atomic publication helpers."""

from __future__ import annotations

from collections.abc import Iterable
import errno
import os
from pathlib import Path
import tempfile
import uuid


_TEMPORARY_FILE_PREFIX = ".git-stage-batch-"


def write_chunks_atomically(
    path: Path,
    chunks: Iterable[bytes],
    *,
    mode: int,
    existing_metadata: os.stat_result | None = None,
) -> None:
    """Publish streamed bytes through a same-directory regular file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=_TEMPORARY_FILE_PREFIX,
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as file_handle:
            for chunk in chunks:
                file_handle.write(chunk)
            file_handle.flush()
            if not _preserve_ownership(file_handle.fileno(), existing_metadata):
                mode &= 0o700
            os.fchmod(file_handle.fileno(), mode)
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def replace_symlink_atomically(path: Path, target: bytes) -> None:
    """Publish a symlink through a same-directory atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    for _attempt in range(100):
        candidate = path.parent / (
            f"{_TEMPORARY_FILE_PREFIX}{uuid.uuid4().hex}.tmp"
        )
        try:
            os.symlink(target, os.fsencode(candidate))
        except FileExistsError:
            continue
        temporary_path = candidate
        break

    if temporary_path is None:
        raise FileExistsError(f"Unable to create temporary symlink for {path}")

    try:
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def fsync_directory(path: Path) -> None:
    """Durably publish directory-entry changes where the platform permits."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_descriptor = os.open(path, flags)
    except OSError as error:
        if os.name == "nt" or error.errno in (errno.EINVAL, errno.ENOTSUP):
            return
        raise
    try:
        try:
            os.fsync(directory_descriptor)
        except OSError as error:
            if error.errno not in (errno.EBADF, errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(directory_descriptor)


def _preserve_ownership(
    file_descriptor: int,
    metadata: os.stat_result | None,
) -> bool:
    if metadata is None or not hasattr(os, "fchown"):
        return True
    try:
        os.fchown(file_descriptor, metadata.st_uid, metadata.st_gid)
    except PermissionError:
        # An unprivileged owner often cannot restore a non-default group. The
        # caller will remove group/other access before publishing the file.
        return False
    return True
