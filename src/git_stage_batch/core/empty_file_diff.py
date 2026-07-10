"""Empty-file helpers for unified diff parsing."""

from __future__ import annotations


NEW_FILE_MODE_MARKER = b"new file mode"
SYNTHETIC_EMPTY_HUNK_HEADER = b"@@ -0,0 +0,0 @@"


def metadata_indicates_new_empty_file(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a newly added empty file."""
    # The parser calls this only after observing that the file has no text
    # hunks and after binary changes have been handled. A newly created file
    # in that state is empty regardless of the repository's hash algorithm.
    return any(NEW_FILE_MODE_MARKER in line for line in metadata_lines)


def synthetic_empty_file_patch_lines(
    old_file_line: bytes,
    new_file_line: bytes,
) -> tuple[bytes, bytes, bytes]:
    """Return synthetic patch lines for an empty-file change."""
    return (
        _line_with_terminator(old_file_line),
        _line_with_terminator(new_file_line),
        SYNTHETIC_EMPTY_HUNK_HEADER + b"\n",
    )


def _line_with_terminator(line: bytes) -> bytes:
    if line.endswith(b"\n"):
        return line
    return line + b"\n"
