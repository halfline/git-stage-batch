"""Unified diff file header helpers."""

from __future__ import annotations

from collections.abc import Iterable

from ..git_paths import decode_path, quoted_token_end, unquote_path_token


OLD_FILE_HEADER_PREFIX = b"--- "
NEW_FILE_HEADER_PREFIX = b"+++ "
OLD_PATH_PREFIX = "a/"
NEW_PATH_PREFIX = "b/"
DEV_NULL_PATH = "/dev/null"


def line_is_old_file_header(line: bytes) -> bool:
    """Return whether a line is the old-file patch header."""
    return line.startswith(OLD_FILE_HEADER_PREFIX)


def line_is_new_file_header(line: bytes) -> bool:
    """Return whether a line is the new-file patch header."""
    return line.startswith(NEW_FILE_HEADER_PREFIX)


def old_file_path_from_header(line: bytes) -> str:
    """Return the normalized old path from a patch file header."""
    return _normalized_patch_path(line, OLD_PATH_PREFIX)


def new_file_path_from_header(line: bytes) -> str:
    """Return the normalized new path from a patch file header."""
    return _normalized_patch_path(line, NEW_PATH_PREFIX)


def line_change_path(old_path: str, new_path: str) -> str:
    """Return the repository path represented by old/new patch headers."""
    if new_path and new_path != DEV_NULL_PATH:
        return new_path
    if old_path and old_path != DEV_NULL_PATH:
        return old_path
    return new_path or old_path or ""


def path_names_repository_file(path: str) -> bool:
    """Return whether a patch path names a file rather than the null device."""
    return path != DEV_NULL_PATH


def patch_targets_file_deletion(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a deleted file path."""
    return any(line.rstrip(b"\n") == b"+++ /dev/null" for line in patch_lines)


def patch_targets_new_file(patch_lines: Iterable[bytes]) -> bool:
    """Return whether patch lines target a newly added file path."""
    return any(line.rstrip(b"\n") == b"--- /dev/null" for line in patch_lines)


def _normalized_patch_path(line: bytes, path_prefix: str) -> str:
    raw_path = line.split(b" ", 1)[1]
    if raw_path.startswith(b'"'):
        raw_path = raw_path[:quoted_token_end(raw_path)]
    else:
        raw_path = raw_path.split(b"\t", 1)[0]
    path = decode_path(unquote_path_token(raw_path))
    if path != DEV_NULL_PATH and path.startswith(path_prefix):
        return path[len(path_prefix):]
    return path
