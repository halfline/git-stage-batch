"""Generic file metadata helpers for unified diff parsing."""

from __future__ import annotations

from ..git_paths import decode_path, unquote_path_token


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


def rename_paths(metadata_lines: list[bytes]) -> tuple[str, str] | None:
    """Return byte-safe old and new paths from rename metadata."""
    old_path = next(
        (
            decode_path(unquote_path_token(line[len(RENAME_FROM_PREFIX):]))
            for line in metadata_lines
            if line.startswith(RENAME_FROM_PREFIX)
        ),
        None,
    )
    new_path = next(
        (
            decode_path(unquote_path_token(line[len(RENAME_TO_PREFIX):]))
            for line in metadata_lines
            if line.startswith(RENAME_TO_PREFIX)
        ),
        None,
    )
    if old_path is None or new_path is None:
        return None
    return old_path, new_path
