"""Bounded JSON output for reusable rewrite plan documents."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Iterable
from pathlib import Path

from ..utils.file_io import (
    AtomicWriteModePolicy,
    write_file_byte_chunks_atomically,
)


def history_json_byte_chunks(value: object) -> Iterable[bytes]:
    """Yield one deterministic indented JSON document."""
    encoder = json.JSONEncoder(indent=2, ensure_ascii=True)
    for chunk in encoder.iterencode(value):
        yield chunk.encode("ascii")
    yield b"\n"


def history_json_sha256(value: object) -> str:
    """Return the digest of the exact persisted JSON representation."""
    digest = hashlib.sha256()
    for chunk in history_json_byte_chunks(value):
        digest.update(chunk)
    return digest.hexdigest()


def history_canonical_json_sha256(value: object) -> str:
    """Hash JSON semantics without allocating one complete serialized string."""
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


def write_history_json_file(
    path: Path,
    value: object,
    *,
    mode_policy: AtomicWriteModePolicy = AtomicWriteModePolicy.PRESERVE_EXISTING,
) -> None:
    """Atomically stream one public reusable JSON document."""
    write_file_byte_chunks_atomically(
        path,
        history_json_byte_chunks(value),
        mode_policy=mode_policy,
    )
