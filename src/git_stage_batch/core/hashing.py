"""Stable hashing of hunks for identity tracking."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..git_paths import unquote_path_token

if TYPE_CHECKING:
    from .models import (
        BinaryFileChange,
        FileModeChange,
        GitlinkChange,
        RenameChange,
        TextFileDeletionChange,
    )


def compute_stable_hunk_hash_from_lines(patch_lines: Iterable[bytes]) -> str:
    """
    Compute a stable identity hash for a one-hunk patch line sequence.

    The hash is based on: path + @@ header + changed lines only (no context).
    Context lines are excluded from the hash to ensure stability when nearby
    code changes or when different -U context values are used.
    This allows tracking which hunks have been processed or blocked
    even as the working tree changes.

    Args:
        patch_lines: Unified diff patch lines as bytes

    Returns:
        SHA-1 hash (hex string)
    """
    selected_path_bytes = b""
    header_bytes = b""
    saw_header = False
    wrote_hash_prefix = False
    wrote_changed_line = False
    digest = hashlib.sha1()

    def write_hash_prefix() -> None:
        nonlocal wrote_hash_prefix
        if wrote_hash_prefix:
            return
        digest.update(selected_path_bytes)
        digest.update(b"\0")
        digest.update(header_bytes)
        digest.update(b"\0")
        wrote_hash_prefix = True

    for line_with_ending in patch_lines:
        # Strip \n for comparison
        line = line_with_ending.rstrip(b'\n')

        if line.startswith(b"+++ "):
            path_value = unquote_path_token(line.split(b" ", 1)[1].strip())
            if path_value != b"/dev/null":
                selected_path_bytes = path_value[2:] if path_value.startswith(b"b/") else path_value
            continue
        if line.startswith(b"--- ") and not selected_path_bytes:
            path_value = unquote_path_token(line.split(b" ", 1)[1].strip())
            if path_value != b"/dev/null":
                selected_path_bytes = path_value[2:] if path_value.startswith(b"a/") else path_value
            continue
        if line.startswith(b"@@ ") and not saw_header:
            header_bytes = line
            saw_header = True
            continue
        if saw_header:
            # Only include actual changes (+ or -), not context lines (space)
            if line and line[0:1] in (b'+', b'-'):
                write_hash_prefix()
                if wrote_changed_line:
                    digest.update(b"\n")
                digest.update(line)
                wrote_changed_line = True

    write_hash_prefix()
    return digest.hexdigest()


def compute_binary_file_hash(binary_change: BinaryFileChange) -> str:
    """
    Compute a stable identity hash for a binary file change.

    Binary files cannot be hashed by content (we don't have the bytes), so we hash
    the file path and change type. This ensures each binary file change is tracked
    uniquely in the blocklist.

    Args:
        binary_change: BinaryFileChange object

    Returns:
        SHA-1 hash (hex string)
    """
    # Use new_path for added files, old_path for deleted files, either for modified
    path = binary_change.path()

    # Hash: "BINARY:" + path + ":" + change_type
    key = f"BINARY:{path}:{binary_change.change_type}".encode(
        "utf-8",
        errors="surrogateescape",
    )
    return hashlib.sha1(key).hexdigest()


def compute_gitlink_change_hash(gitlink_change: GitlinkChange) -> str:
    """Compute a stable identity hash for an atomic gitlink change."""
    parts = [
        "gitlink",
        gitlink_change.old_path,
        gitlink_change.new_path,
        gitlink_change.old_oid or "",
        gitlink_change.new_oid or "",
        gitlink_change.change_type,
    ]
    return hashlib.sha256(
        "\0".join(parts).encode("utf-8", errors="surrogateescape")
    ).hexdigest()


def compute_file_mode_change_hash(mode_change: FileModeChange) -> str:
    """Compute a stable identity hash for an executable-mode change."""
    parts = [
        "file-mode",
        mode_change.file_path,
        mode_change.old_mode,
        mode_change.new_mode,
    ]
    return hashlib.sha256(
        "\0".join(parts).encode("utf-8", errors="surrogateescape")
    ).hexdigest()


def compute_rename_change_hash(rename_change: RenameChange) -> str:
    """Compute a stable identity hash for an atomic rename change."""
    parts = [
        "rename",
        rename_change.old_path,
        rename_change.new_path,
    ]
    return hashlib.sha256(
        "\0".join(parts).encode("utf-8", errors="surrogateescape")
    ).hexdigest()


def compute_text_file_deletion_hash(deletion_change: TextFileDeletionChange) -> str:
    """Compute a stable identity hash for an atomic text file deletion."""
    parts = [
        "text-deletion",
        deletion_change.old_path,
        deletion_change.new_path,
    ]
    return hashlib.sha256(
        "\0".join(parts).encode("utf-8", errors="surrogateescape")
    ).hexdigest()
