"""File I/O utilities for git-stage-batch."""

from __future__ import annotations

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
    """Write text to a file, creating parent directories as needed.

    Args:
        path: Path to the file to write
        data: Text content to write
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", errors="surrogateescape")


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
    content = read_text_file_contents(path)
    if not content:
        return []
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return sorted(set(lines))


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
