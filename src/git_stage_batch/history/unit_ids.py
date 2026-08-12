"""Stable identities for source-bound history patch units."""

from __future__ import annotations

import hashlib


def history_unit_id(source_commit: str, patch_id: str) -> str:
    """Bind an exact patch-unit identity to its source commit object."""
    digest = hashlib.sha256()
    digest.update(b"git-stage-batch-rewrite-unit-v1\0")
    digest.update(source_commit.encode("ascii"))
    digest.update(b"\0")
    digest.update(patch_id.encode("ascii"))
    return digest.hexdigest()
