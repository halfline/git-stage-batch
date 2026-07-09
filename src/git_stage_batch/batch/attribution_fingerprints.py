"""Content fingerprints used by attribution matching."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib

from ..core.buffer import buffer_matches
from ..utils.repository_buffers import load_git_blob_as_buffer


@dataclass(frozen=True)
class ContentFingerprint:
    """Byte count and digest for matching content without retaining it."""

    byte_count: int
    sha1: str


def fingerprint_chunks(chunks: Iterable[bytes]) -> ContentFingerprint:
    """Return a streaming content fingerprint for byte chunks."""
    digest = hashlib.sha1()
    byte_count = 0
    for chunk in chunks:
        digest.update(chunk)
        byte_count += len(chunk)
    return ContentFingerprint(byte_count=byte_count, sha1=digest.hexdigest())


def fingerprint_bytes(content: bytes | None) -> ContentFingerprint | None:
    """Return a content fingerprint for optional in-memory bytes."""
    if content is None:
        return None
    return fingerprint_chunks([content])


def fingerprint_git_blob(blob_hash: str) -> ContentFingerprint | None:
    """Return a content fingerprint for a Git blob when the blob exists."""
    try:
        with load_git_blob_as_buffer(blob_hash) as blob_buffer:
            return fingerprint_chunks(blob_buffer.byte_chunks())
    except RuntimeError:
        return None


def fingerprint_numbered_lines(
    lines: Sequence[bytes],
    line_numbers: Iterable[int],
) -> ContentFingerprint:
    """Return a content fingerprint for selected 1-based line numbers."""
    return fingerprint_chunks(lines[line_number - 1] for line_number in line_numbers)


def blob_matches_content(blob_hash: str, content: bytes) -> bool:
    """Return whether a Git blob has the supplied byte content."""
    try:
        with load_git_blob_as_buffer(blob_hash) as blob_buffer:
            return buffer_matches(blob_buffer, content)
    except RuntimeError:
        return False
