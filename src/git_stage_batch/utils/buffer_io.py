"""Atomic filesystem publication for core byte buffers."""

from __future__ import annotations

from pathlib import Path
import stat

from ..core.buffer import BufferInput, buffer_byte_chunks
from .atomic_write import replace_symlink_atomically, write_chunks_atomically


def write_buffer_to_path(path: str | Path, buffer: BufferInput) -> None:
    """Write buffer bytes to a path, creating parent directories as needed."""
    file_path = Path(path)
    if file_path.is_symlink():
        target = b"".join(buffer_byte_chunks(buffer))
        replace_symlink_atomically(file_path, target)
        return

    _write_regular_file_atomically(file_path, buffer)


def write_buffer_to_working_tree_path(
    path: str | Path,
    buffer: BufferInput,
    *,
    mode: str | None = None,
) -> None:
    """Write buffer bytes as a Git working-tree path with the given mode."""
    file_path = Path(path)
    if mode == "120000":
        target = b"".join(buffer_byte_chunks(buffer))
        replace_symlink_atomically(file_path, target)
        return

    requested_mode = None
    if mode == "100755":
        requested_mode = 0o755
    elif mode == "100644":
        requested_mode = 0o644
    _write_regular_file_atomically(file_path, buffer, mode=requested_mode)


def _write_regular_file_atomically(
    file_path: Path,
    buffer: BufferInput,
    *,
    mode: int | None = None,
) -> None:
    """Stream a regular file through a same-directory atomic replacement."""
    try:
        metadata = file_path.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and not stat.S_ISREG(metadata.st_mode):
        metadata = None
    replacement_mode = (
        mode
        if mode is not None
        else stat.S_IMODE(metadata.st_mode) if metadata is not None else 0o644
    )
    write_chunks_atomically(
        file_path,
        buffer_byte_chunks(buffer),
        mode=replacement_mode,
        existing_metadata=metadata,
    )
