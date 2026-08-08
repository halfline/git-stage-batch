"""Stable identities for exact fixup units."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .models import FixupUnitKind


def fixup_unit_id(
    kind: FixupUnitKind,
    path: str,
    payload_chunks: Iterable[bytes],
) -> str:
    """Return a content-derived identity for one exact fixup unit."""
    digest = hashlib.sha256()
    digest.update(b"git-stage-batch-fixup-unit-v1\0")
    digest.update(kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(path.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    for chunk in payload_chunks:
        digest.update(chunk)
    return digest.hexdigest()
