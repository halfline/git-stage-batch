"""Unified diff header helpers."""

from __future__ import annotations

from ..git_paths import decode_path, quoted_token_end, unquote_path_token


DIFF_GIT_PREFIX = b"diff --git "
OLD_PATH_PREFIX = b"a/"
NEW_PATH_PREFIX = b"b/"
NEW_PATH_MARKER = b" b/"


def line_is_diff_git_header(line: bytes) -> bool:
    """Return whether a line starts a git file diff."""
    return line.startswith(DIFF_GIT_PREFIX)


def diff_git_paths(line: bytes) -> tuple[str, str] | None:
    """Return old and new paths from a git file diff header."""
    if not line_is_diff_git_header(line):
        return None

    rest = line[len(DIFF_GIT_PREFIX):]
    if rest.startswith(b'"'):
        old_end = quoted_token_end(rest)
        if rest[old_end:old_end + 1] != b" ":
            return None
        old_token = rest[:old_end]
        new_token = rest[old_end + 1:]
    else:
        # Git leaves ordinary spaces unquoted. Fixed prefixes make the final
        # separator ambiguous when either path itself contains " b/". An
        # unchanged pathname is the common case and identifies its boundary
        # exactly; patch/rename metadata later supplies distinct pathnames.
        separators = []
        offset = 0
        while True:
            separator = rest.find(b" " + NEW_PATH_PREFIX, offset)
            if separator < 0:
                break
            separators.append(separator)
            offset = separator + 1
        if not separators:
            return None
        separator = separators[-1]
        for candidate in separators:
            candidate_old = rest[len(OLD_PATH_PREFIX):candidate]
            candidate_new = rest[candidate + 1 + len(NEW_PATH_PREFIX):]
            if candidate_old == candidate_new:
                separator = candidate
                break
        old_token = rest[:separator]
        new_token = rest[separator + 1:]

    old_path = unquote_path_token(old_token)
    new_path = unquote_path_token(new_token)
    if not old_path.startswith(OLD_PATH_PREFIX) or not new_path.startswith(
        NEW_PATH_PREFIX
    ):
        return None
    return (
        decode_path(old_path[len(OLD_PATH_PREFIX):]),
        decode_path(new_path[len(NEW_PATH_PREFIX):]),
    )
