"""Generic file metadata helpers for unified diff parsing."""

from __future__ import annotations


DELETED_FILE_MODE_PREFIX = b"deleted file mode "
RENAME_FROM_PREFIX = b"rename from "
RENAME_TO_PREFIX = b"rename to "


def metadata_indicates_rename(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a path rename."""
    has_rename_from = any(
        line.startswith(RENAME_FROM_PREFIX) for line in metadata_lines
    )
    has_rename_to = any(line.startswith(RENAME_TO_PREFIX) for line in metadata_lines)
    return has_rename_from and has_rename_to


def metadata_indicates_deleted_file(metadata_lines: list[bytes]) -> bool:
    """Return whether diff metadata describes a deleted file."""
    return any(
        line.startswith(DELETED_FILE_MODE_PREFIX) for line in metadata_lines
    )
