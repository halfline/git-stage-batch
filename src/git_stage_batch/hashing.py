"""Compute stable hashes for hunks to track their identity."""

from __future__ import annotations

import hashlib


def compute_stable_hunk_hash(patch_text: str) -> str:
    """Compute a stable SHA1 hash of a patch.

    This hash uniquely identifies a hunk based on its content. The same
    hunk will always produce the same hash, allowing us to track which
    hunks have been processed across sessions.

    Args:
        patch_text: The patch text in unified diff format

    Returns:
        SHA1 hash as a hexadecimal string
    """
    return hashlib.sha1(patch_text.encode("utf-8")).hexdigest()
