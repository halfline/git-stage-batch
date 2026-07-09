"""Unified diff header helpers."""

from __future__ import annotations


DIFF_GIT_PREFIX = b"diff --git "
OLD_PATH_PREFIX = b"a/"
NEW_PATH_MARKER = b" b/"


def line_is_diff_git_header(line: bytes) -> bool:
    """Return whether a line starts a git file diff."""
    return line.startswith(DIFF_GIT_PREFIX)


def diff_git_paths(line: bytes) -> tuple[str, str] | None:
    """Return old and new paths from a git file diff header."""
    if not line_is_diff_git_header(line):
        return None

    rest = line[len(DIFF_GIT_PREFIX):]
    old_path_start = rest.find(OLD_PATH_PREFIX)
    new_path_start = rest.find(NEW_PATH_MARKER)

    if old_path_start == -1 or new_path_start == -1:
        return None

    old_path = rest[old_path_start + len(OLD_PATH_PREFIX):new_path_start]
    new_path = rest[new_path_start + len(NEW_PATH_MARKER):]
    return old_path.decode("utf-8"), new_path.decode("utf-8")
