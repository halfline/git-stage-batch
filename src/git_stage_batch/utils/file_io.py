"""File I/O utilities for git-stage-batch."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from collections.abc import Collection
from enum import Enum
from pathlib import Path
from typing import Iterable

from ..exceptions import CommandError
from ..git_paths import decode_path, encode_path, nul_records
from ..i18n import _


class AtomicWriteModePolicy(Enum):
    """Permission policy for an atomically replaced file."""

    PRIVATE = "private"
    PRESERVE_EXISTING = "preserve-existing"
    CALLER_SUPPLIED = "caller-supplied"


PRIVATE_FILE_MODE = 0o600
PROJECT_FILE_MODE = 0o644
_PATH_LIST_MAGIC = b"\0git-stage-batch-path-list-v1\0"


def read_text_file_contents(path: Path) -> str:
    """Read a file's text contents with UTF-8 encoding.

    Args:
        path: Path to the file to read

    Returns:
        File contents as string, or empty string if file doesn't exist
    """
    return (
        path.read_text(encoding="utf-8", errors="surrogateescape")
        if path.exists()
        else ""
    )


def write_text_file_contents(
    path: Path,
    data: str,
    *,
    mode_policy: AtomicWriteModePolicy = AtomicWriteModePolicy.PRIVATE,
    mode: int | None = None,
) -> None:
    """Atomically write text, creating parent directories as needed.

    Args:
        path: Path to the file to write
        data: Text content to write
    """
    _write_file_contents_atomically(
        path,
        data.encode("utf-8", errors="surrogateescape"),
        mode_policy=mode_policy,
        mode=mode,
    )


def stream_text_file_lines(path: Path) -> Iterable[str]:
    """Yield a text file's lines, or no lines if it does not exist."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="surrogateescape") as file_handle:
        yield from file_handle


def stream_nonblank_text_file_lines(path: Path) -> Iterable[str]:
    """Yield stripped nonblank lines from a text file."""
    for line in stream_text_file_lines(path):
        stripped = line.strip()
        if stripped:
            yield stripped


def read_text_file_line_set(path: Path) -> set[str]:
    """Read stripped nonblank text lines into a set."""
    return set(stream_nonblank_text_file_lines(path))


def count_nonblank_text_file_lines(path: Path) -> int:
    """Count nonblank text lines without reading the whole file."""
    return sum(1 for _line in stream_nonblank_text_file_lines(path))


def write_file_bytes(
    path: Path,
    data: bytes,
    *,
    mode_policy: AtomicWriteModePolicy = AtomicWriteModePolicy.PRIVATE,
    mode: int | None = None,
) -> None:
    """Atomically write raw bytes, creating parent directories as needed."""
    _write_file_contents_atomically(
        path,
        data,
        mode_policy=mode_policy,
        mode=mode,
    )


def _existing_file_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise CommandError(
            _(
                "Refusing to replace symlink '{path}' with a regular file. "
                "Remove the symlink or update its target explicitly."
            ).format(path=path)
        )
    return metadata


def _replacement_mode(
    metadata: os.stat_result | None,
    mode_policy: AtomicWriteModePolicy,
    mode: int | None,
) -> int:
    if mode_policy is AtomicWriteModePolicy.PRIVATE:
        if mode is not None:
            raise ValueError(
                "private atomic writes do not accept a caller-supplied mode"
            )
        return PRIVATE_FILE_MODE
    if mode_policy is AtomicWriteModePolicy.PRESERVE_EXISTING:
        if metadata is not None:
            return stat.S_IMODE(metadata.st_mode)
        return PROJECT_FILE_MODE if mode is None else mode
    if mode_policy is AtomicWriteModePolicy.CALLER_SUPPLIED:
        if mode is None:
            raise ValueError("caller-supplied atomic writes require a mode")
        return mode
    raise ValueError(f"unsupported atomic write mode policy: {mode_policy!r}")


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


def _write_file_contents_atomically(
    path: Path,
    data: bytes,
    *,
    mode_policy: AtomicWriteModePolicy,
    mode: int | None,
) -> None:
    """Replace one state file without exposing partial contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = _existing_file_metadata(path)
    replacement_mode = _replacement_mode(metadata, mode_policy, mode)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as file_handle:
            file_handle.write(data)
            file_handle.flush()
            ownership_preserved = _preserve_ownership(file_handle.fileno(), metadata)
            if not ownership_preserved:
                replacement_mode &= 0o700
            os.fchmod(file_handle.fileno(), replacement_mode)
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def path_is_empty(path: Path) -> bool:
    """Return whether a file contains no bytes."""
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            if chunk:
                return False
    return True


def append_lines_to_file(path: Path, lines: Iterable[str]) -> None:
    """Append lines to a file, creating parent directories as needed.

    Each line is normalized to end with a single newline character.

    Args:
        path: Path to the file
        lines: Lines to append (newlines will be normalized)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        PRIVATE_FILE_MODE,
    )
    os.fchmod(file_descriptor, PRIVATE_FILE_MODE)
    with os.fdopen(
        file_descriptor,
        "a",
        encoding="utf-8",
        errors="surrogateescape",
    ) as file_handle:
        for line in lines:
            file_handle.write(str(line).rstrip() + "\n")


def read_file_paths_file(path: Path) -> list[str]:
    """Read a file containing one path per line, returning a deduplicated sorted list.

    Args:
        path: Path to file containing paths

    Returns:
        Sorted list of unique paths
    """
    if not path.exists():
        return []

    contents = path.read_bytes()
    if contents.startswith(_PATH_LIST_MAGIC):
        encoded_paths = nul_records(contents[len(_PATH_LIST_MAGIC) :])
        return sorted({decode_path(encoded_path) for encoded_path in encoded_paths})

    return sorted(read_text_file_line_set(path))


def _path_requires_lossless_list_encoding(path: str) -> bool:
    return "\n" in path or "\r" in path or path != path.strip()


def write_file_paths_file(path: Path, file_paths: Iterable[str]) -> None:
    """Write file paths to a file, one per line, sorted and deduplicated.

    Args:
        path: Path to file to write
        file_paths: Paths to write
    """
    unique_paths = sorted(set(file_paths))
    if any(_path_requires_lossless_list_encoding(path) for path in unique_paths):
        contents = _PATH_LIST_MAGIC + b"".join(
            encode_path(path) + b"\0" for path in unique_paths
        )
        write_file_bytes(path, contents)
        return

    content = "\n".join(unique_paths)
    if unique_paths:
        content += "\n"
    write_text_file_contents(path, content)


def append_file_path_to_file(path: Path, file_path: str) -> None:
    """Append a file path to a list file, preventing duplicates.

    Args:
        path: Path to list file
        file_path: File path to append
    """
    existing_paths = read_file_paths_file(path)
    if file_path not in existing_paths:
        existing_paths.append(file_path)
        write_file_paths_file(path, existing_paths)


def is_path_blocked(path: str, blocked_files: Collection[str]) -> bool:
    """Return True when path is covered by the blocked-files list.

    A negation entry (!path) takes precedence over all other entries.
    An entry covers path if it equals path exactly, or if the entry ends
    with '/' and path starts with that prefix (directory match).
    """
    # Exact and directory negations are authoritative in the compatibility
    # state format, even when callers have loaded it into a set.
    for entry in blocked_files:
        if not entry.startswith("!"):
            continue
        included = entry[1:]
        if path == included or (included.endswith("/") and path.startswith(included)):
            return False
    for entry in reversed(list(blocked_files)):
        if entry.startswith("!"):
            continue
        elif path == entry or (entry.endswith("/") and path.startswith(entry)):
            return True
    return False


def remove_file_path_from_file(state_file_path: Path, file_path: str) -> None:
    """Remove a file path from a list file.

    Args:
        state_file_path: Path to list file
        file_path: File path to remove
    """
    existing_paths = read_file_paths_file(state_file_path)
    if file_path in existing_paths:
        existing_paths.remove(file_path)
        write_file_paths_file(state_file_path, existing_paths)
