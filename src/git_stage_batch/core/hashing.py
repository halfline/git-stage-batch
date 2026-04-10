"""Stable hashing of hunks for identity tracking."""

from __future__ import annotations

import hashlib


def compute_stable_hunk_hash(patch_bytes: bytes) -> str:
    """
    Compute a stable identity hash for a one-hunk patch.

    The hash is based on: path + @@ header + changed lines only (no context).
    Context lines are excluded from the hash to ensure stability when nearby
    code changes or when different -U context values are used.
    This allows tracking which hunks have been processed or blocked
    even as the working tree changes.

    Args:
        patch_bytes: Unified diff patch as bytes

    Returns:
        SHA-1 hash (hex string)
    """
    selected_path_bytes = b""
    header_bytes = b""
    body_lines_bytes: list[bytes] = []
    saw_header = False

    # Use splitlines(keepends=True) to preserve exact line endings for hashing
    for line_with_ending in patch_bytes.splitlines(keepends=True):
        # Strip \n for comparison
        line = line_with_ending.rstrip(b'\n')

        if line.startswith(b"+++ "):
            path_value = line.split(b" ", 1)[1].strip()
            if path_value != b"/dev/null":
                selected_path_bytes = path_value[2:] if path_value.startswith(b"b/") else path_value
            continue
        if line.startswith(b"--- ") and not selected_path_bytes:
            path_value = line.split(b" ", 1)[1].strip()
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
                body_lines_bytes.append(line)

    # Build hash key from bytes (no encoding needed)
    key = selected_path_bytes + b"\0" + header_bytes + b"\0" + b'\n'.join(body_lines_bytes)
    return hashlib.sha1(key).hexdigest()
