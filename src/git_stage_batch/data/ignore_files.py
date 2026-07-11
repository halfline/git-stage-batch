"""Repository ignore-file editing helpers."""

from __future__ import annotations

from pathlib import Path

from ..utils.file_io import (
    AtomicWriteModePolicy,
    PROJECT_FILE_MODE,
    read_text_file_contents,
    write_text_file_contents,
)
from ..utils.git_repository import get_git_directory_path, get_git_repository_root_path


def get_gitignore_path() -> Path:
    """Get the path to the repository's .gitignore file.

    Returns:
        Path to .gitignore
    """
    return get_git_repository_root_path() / ".gitignore"


def get_local_exclude_path() -> Path:
    """Get the path to the repository's .git/info/exclude file.

    Returns:
        Path to .git/info/exclude
    """
    return get_git_directory_path() / "info" / "exclude"


def _write_repository_ignore_file(path: Path, content: str) -> None:
    write_text_file_contents(
        path,
        content,
        mode_policy=AtomicWriteModePolicy.PRESERVE_EXISTING,
        mode=PROJECT_FILE_MODE,
    )


def read_gitignore_lines() -> list[str]:
    """Read .gitignore file, returning lines preserving original formatting.

    Returns:
        List of lines from .gitignore with original formatting
    """
    gitignore_path = get_gitignore_path()
    if not gitignore_path.exists():
        return []
    content = read_text_file_contents(gitignore_path)
    return content.splitlines(keepends=True)


def write_gitignore_lines(lines: list[str]) -> None:
    """Write lines to .gitignore, preserving formatting.

    Args:
        lines: Lines to write to .gitignore
    """
    gitignore_path = get_gitignore_path()
    content = "".join(lines)
    _write_repository_ignore_file(gitignore_path, content)


def add_file_to_gitignore(file_path: str) -> None:
    """Add a file path to .gitignore.

    Args:
        file_path: File path to add
    """
    lines = read_gitignore_lines()

    file_path_normalized = file_path.rstrip("\n")
    for line in lines:
        if line.rstrip("\n") == file_path_normalized:
            return

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f"{file_path}\n")
    write_gitignore_lines(lines)


def remove_file_from_gitignore(file_path: str) -> bool:
    """Remove a file path from .gitignore.

    Args:
        file_path: File path to remove

    Returns:
        True if removed, False if not found
    """
    lines = read_gitignore_lines()
    file_path_normalized = file_path.rstrip("\n")

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") == file_path_normalized:
            del lines[i]
            removed = True
            continue
        i += 1

    if removed:
        write_gitignore_lines(lines)

    return removed


def add_file_to_local_exclude(file_path: str) -> None:
    """Add a file path to .git/info/exclude.

    Args:
        file_path: File path to add
    """
    exclude_path = get_local_exclude_path()
    exclude_path.parent.mkdir(parents=True, exist_ok=True)

    if exclude_path.exists():
        content = read_text_file_contents(exclude_path)
        lines = content.splitlines(keepends=True)
    else:
        lines = []

    file_path_normalized = file_path.rstrip("\n")
    for line in lines:
        if line.rstrip("\n") == file_path_normalized:
            return

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f"{file_path}\n")
    _write_repository_ignore_file(exclude_path, "".join(lines))


def remove_file_from_local_exclude(file_path: str) -> bool:
    """Remove a file path from .git/info/exclude.

    Args:
        file_path: File path to remove

    Returns:
        True if removed, False if not found
    """
    exclude_path = get_local_exclude_path()
    if not exclude_path.exists():
        return False

    content = read_text_file_contents(exclude_path)
    lines = content.splitlines(keepends=True)
    file_path_normalized = file_path.rstrip("\n")

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") == file_path_normalized:
            del lines[i]
            removed = True
            continue
        i += 1

    if removed:
        _write_repository_ignore_file(exclude_path, "".join(lines))

    return removed


def promote_directory_to_glob_in_gitignore(dir_path: str) -> bool:
    """Replace a dir/ entry with dir/** in .gitignore for negation patterns.

    Returns True when dir/ or dir/** is present, and False when the directory
    has no entry in .gitignore.
    """
    lines = read_gitignore_lines()
    dir_entry = dir_path.rstrip("/") + "/"
    glob_entry = dir_path.rstrip("/") + "/**"

    if any(line.rstrip("\n") == glob_entry for line in lines):
        return True

    found = False
    for i, line in enumerate(lines):
        if line.rstrip("\n") == dir_entry:
            lines[i] = glob_entry + ("\n" if line.endswith("\n") else "")
            found = True

    if found:
        write_gitignore_lines(lines)
    return found


def promote_directory_to_glob_in_local_exclude(dir_path: str) -> bool:
    """Replace a dir/ entry with dir/** in .git/info/exclude.

    Returns True when dir/ or dir/** is present, and False when the directory
    has no entry in .git/info/exclude.
    """
    exclude_path = get_local_exclude_path()
    if not exclude_path.exists():
        return False

    content = read_text_file_contents(exclude_path)
    lines = content.splitlines(keepends=True)
    dir_entry = dir_path.rstrip("/") + "/"
    glob_entry = dir_path.rstrip("/") + "/**"

    if any(line.rstrip("\n") == glob_entry for line in lines):
        return True

    found = False
    for i, line in enumerate(lines):
        if line.rstrip("\n") == dir_entry:
            lines[i] = glob_entry + ("\n" if line.endswith("\n") else "")
            found = True

    if found:
        _write_repository_ignore_file(exclude_path, "".join(lines))
    return found
