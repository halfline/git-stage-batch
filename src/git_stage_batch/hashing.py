"""Stable hashing of hunks for identity tracking."""

from __future__ import annotations

import hashlib


def compute_stable_hunk_hash(patch_text: str) -> str:
    """
    Compute a stable identity hash for a one-hunk patch.

    The hash is based on: path + @@ header + changed lines only (no context).
    Context lines are excluded from the hash to ensure stability when nearby
    code changes.
    This allows tracking which hunks have been processed or blocked
    even as the working tree changes.
    """
    selected_path = ""
    header_text = ""
    body_lines: list[str] = []
    saw_header = False

    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            path_value = line.split(" ", 1)[1].strip()
            if path_value != "/dev/null":
                selected_path = path_value[2:] if path_value.startswith("b/") else path_value
            continue
        if line.startswith("--- ") and not selected_path:
            path_value = line.split(" ", 1)[1].strip()
            if path_value != "/dev/null":
                selected_path = path_value[2:] if path_value.startswith("a/") else path_value
            continue
        if line.startswith("@@ ") and not saw_header:
            header_text = line.rstrip("\n")
            saw_header = True
            continue
        if saw_header:
            # Only include actual changes (+ or -), not context lines (space)
            if line and line[0] in ('+', '-'):
                body_lines.append(line)

    key = f"{selected_path}\0{header_text}\0{'\n'.join(body_lines)}"
    return hashlib.sha1(key.encode("utf-8", errors="surrogateescape")).hexdigest()
