"""Generic file metadata helpers for unified diff parsing."""

from __future__ import annotations

from ..git_paths import decode_path, unquote_path_token


DELETED_FILE_MODE_PREFIX = b"deleted file mode "
OLD_MODE_PREFIX = b"old mode "
NEW_MODE_PREFIX = b"new mode "
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


def executable_mode_change(
    metadata_lines: list[bytes],
) -> tuple[str, str] | None:
    """Return a regular-file executable-bit transition, if present."""
    old_mode = next(
        (
            line[len(OLD_MODE_PREFIX):].decode("ascii", errors="replace")
            for line in metadata_lines
            if line.startswith(OLD_MODE_PREFIX)
        ),
        None,
    )
    new_mode = next(
        (
            line[len(NEW_MODE_PREFIX):].decode("ascii", errors="replace")
            for line in metadata_lines
            if line.startswith(NEW_MODE_PREFIX)
        ),
        None,
    )
    regular_modes = {"100644", "100755"}
    if (
        old_mode not in regular_modes
        or new_mode not in regular_modes
        or old_mode == new_mode
    ):
        return None
    return old_mode, new_mode


def file_type_change(metadata_lines: list[bytes]) -> tuple[str, str] | None:
    """Return a non-executable file-type transition, if present."""
    old_mode = next(
        (line[len(OLD_MODE_PREFIX):].decode("ascii") for line in metadata_lines if line.startswith(OLD_MODE_PREFIX)),
        None,
    )
    new_mode = next(
        (line[len(NEW_MODE_PREFIX):].decode("ascii") for line in metadata_lines if line.startswith(NEW_MODE_PREFIX)),
        None,
    )
    if old_mode is None or new_mode is None:
        return None
    if {old_mode, new_mode} <= {"100644", "100755"}:
        return None
    return old_mode, new_mode
