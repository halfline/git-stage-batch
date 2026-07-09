"""Binary-file helpers for unified diff parsing."""

from __future__ import annotations


BINARY_FILES_MARKER = b"Binary files"
DEV_NULL_PATH = b"/dev/null"
NEW_BINARY_PATH_MARKER = b"and b/"
OLD_BINARY_PATH_MARKER = b"a/"


def binary_file_diff_line(metadata_lines: list[bytes]) -> bytes | None:
    """Return the binary-file metadata line when present."""
    return next((line for line in metadata_lines if BINARY_FILES_MARKER in line), None)


def metadata_indicates_binary_file(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a binary-file change."""
    return binary_file_diff_line(metadata_lines) is not None


def binary_change_type(metadata_lines: list[bytes]) -> str:
    """Derive added/modified/deleted from binary-file diff metadata."""
    binary_line = binary_file_diff_line(metadata_lines) or b"Binary files differ"

    if DEV_NULL_PATH in binary_line and NEW_BINARY_PATH_MARKER in binary_line:
        return "added"
    if OLD_BINARY_PATH_MARKER in binary_line and DEV_NULL_PATH in binary_line:
        return "deleted"
    return "modified"
