"""Empty-file helpers for unified diff parsing."""

from __future__ import annotations


EMPTY_BLOB_SHORT_HASH = b"e69de29"
NEW_FILE_MODE_MARKER = b"new file mode"
SYNTHETIC_EMPTY_HUNK_HEADER = b"@@ -0,0 +0,0 @@"


def metadata_indicates_new_empty_file(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a newly added empty file."""
    is_new_file = any(NEW_FILE_MODE_MARKER in line for line in metadata_lines)
    is_empty = any(EMPTY_BLOB_SHORT_HASH in line for line in metadata_lines)
    return is_new_file and is_empty


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
