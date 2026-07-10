"""File I/O utilities for git-stage-batch."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Iterable


def read_text_file_contents(path: Path) -> str:
    """Read a file's text contents with UTF-8 encoding.

    Args:
        path: Path to the file to read

    Returns:
        File contents as string, or empty string if file doesn't exist
    """
    return path.read_text(encoding="utf-8", errors="surrogateescape") if path.exists() else ""


def write_text_file_contents(path: Path, data: str) -> None:
    """Atomically write text, creating parent directories as needed.

    Args:
        path: Path to the file to write
        data: Text content to write
    """
    _write_file_contents_atomically(
        path,
        data.encode("utf-8", errors="surrogateescape"),
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


def write_file_bytes(path: Path, data: bytes) -> None:
    """Atomically write raw bytes, creating parent directories as needed."""
    _write_file_contents_atomically(path, data)


def _write_file_contents_atomically(path: Path, data: bytes) -> None:
    """Replace one state file without exposing partial contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
            os.fsync(file_handle.fileno())
        os.replace(temporary_path, path)
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
    with path.open("a", encoding="utf-8", errors="surrogateescape") as file_handle:
        for line in lines:
            file_handle.write(str(line).rstrip() + "\n")


def read_file_paths_file(path: Path) -> list[str]:
    """Read a file containing one path per line, returning a deduplicated sorted list.

    Args:
        path: Path to file containing paths

    Returns:
        Sorted list of unique paths
    """
    return sorted(read_text_file_line_set(path))


def write_file_paths_file(path: Path, file_paths: Iterable[str]) -> None:
    """Write file paths to a file, one per line, sorted and deduplicated.

    Args:
        path: Path to file to write
        file_paths: Paths to write
    """
    unique_paths = sorted(set(file_paths))
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
    if f"!{path}" in blocked_files:
        return False
    if path in blocked_files:
        return True
    return any(path.startswith(entry) for entry in blocked_files if entry.endswith("/"))


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
