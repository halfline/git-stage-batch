"""Repository ignore-file editing helpers."""

from __future__ import annotations

from pathlib import Path

from ..exceptions import CommandError
from ..i18n import _
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


def literal_ignore_pattern(file_path: str) -> str:
    """Return a gitignore pattern that matches one literal repository path."""
    if "\n" in file_path or "\r" in file_path:
        raise CommandError(
            _("Cannot write an ignore rule for a path containing a line break.")
        )

    trailing_space_start = len(file_path.rstrip(" "))
    escaped = []
    for index, character in enumerate(file_path):
        if (
            character in "\\*?["
            or (index == 0 and character in "#!")
            or (character == " " and index >= trailing_space_start)
        ):
            escaped.append("\\")
        escaped.append(character)
    return "/" + "".join(escaped)


def _literal_ignore_pattern_variants(file_path: str) -> set[str]:
    """Return current and legacy encodings for one literal path."""
    anchored = literal_ignore_pattern(file_path)
    return {
        anchored,
        anchored.removeprefix("/"),
        file_path.rstrip("\n"),
    }


def _append_ignore_entry(lines: list[str], entry: str) -> bool:
    if any(line.rstrip("\n") == entry for line in lines):
        return False
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{entry}\n")
    return True


def add_file_to_gitignore(file_path: str) -> None:
    """Add a file path to .gitignore.

    Args:
        file_path: File path to add
    """
    lines = read_gitignore_lines()
    if _append_ignore_entry(lines, literal_ignore_pattern(file_path)):
        write_gitignore_lines(lines)


def add_pattern_to_gitignore(pattern: str) -> None:
    """Add an intentional gitignore pattern without literal escaping."""
    lines = read_gitignore_lines()
    if _append_ignore_entry(lines, pattern.rstrip("\n")):
        write_gitignore_lines(lines)


def remove_file_from_gitignore(file_path: str) -> bool:
    """Remove a file path from .gitignore.

    Args:
        file_path: File path to remove

    Returns:
        True if removed, False if not found
    """
    lines = read_gitignore_lines()
    file_path_patterns = _literal_ignore_pattern_variants(file_path)

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") in file_path_patterns:
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

    if _append_ignore_entry(lines, literal_ignore_pattern(file_path)):
        _write_repository_ignore_file(exclude_path, "".join(lines))


def add_pattern_to_local_exclude(pattern: str) -> None:
    """Add an intentional local exclude pattern without literal escaping."""
    exclude_path = get_local_exclude_path()
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        read_text_file_contents(exclude_path).splitlines(keepends=True)
        if exclude_path.exists()
        else []
    )
    if _append_ignore_entry(lines, pattern.rstrip("\n")):
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
    file_path_patterns = _literal_ignore_pattern_variants(file_path)

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") in file_path_patterns:
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
    directory_patterns = _literal_ignore_pattern_variants(dir_path.rstrip("/"))
    dir_entries = {pattern + "/" for pattern in directory_patterns}
    glob_entries = {pattern + "/**" for pattern in directory_patterns}
    known_entries = dir_entries | glob_entries
    glob_entry = literal_ignore_pattern(dir_path.rstrip("/")) + "/**"

    found = False
    changed = False
    for i, line in enumerate(lines):
        if line.rstrip("\n") not in known_entries:
            continue
        replacement = glob_entry + ("\n" if line.endswith("\n") else "")
        found = True
        if replacement != line:
            lines[i] = replacement
            changed = True

    if changed:
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
    directory_patterns = _literal_ignore_pattern_variants(dir_path.rstrip("/"))
    dir_entries = {pattern + "/" for pattern in directory_patterns}
    glob_entries = {pattern + "/**" for pattern in directory_patterns}
    known_entries = dir_entries | glob_entries
    glob_entry = literal_ignore_pattern(dir_path.rstrip("/")) + "/**"

    found = False
    changed = False
    for i, line in enumerate(lines):
        if line.rstrip("\n") not in known_entries:
            continue
        replacement = glob_entry + ("\n" if line.endswith("\n") else "")
        found = True
        if replacement != line:
            lines[i] = replacement
            changed = True

    if changed:
        _write_repository_ignore_file(exclude_path, "".join(lines))
    return found
