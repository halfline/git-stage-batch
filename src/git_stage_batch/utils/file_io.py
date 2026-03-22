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
