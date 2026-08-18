"""Snapshot-bound coordinate values used by staging and batch domains.

Git's external formats use one-based line numbers and inclusive ranges.  The
domain model deliberately does not: positions are zero-based boundaries and
spans are half-open.  Conversion belongs at parser and metadata adapters.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
import hashlib
from collections.abc import Sequence
from typing import Callable, Generic, Iterable, Iterator, Protocol, TypeVar, cast


class BaselineSpace:
    """Marker for coordinates in a batch baseline snapshot."""


class BatchSourceSpace:
    """Marker for coordinates in a durable batch-source snapshot."""


class WorktreeSpace:
    """Marker for coordinates in an observed working-tree snapshot."""


Space = TypeVar("Space")


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    """Stable identity for one exact file-content snapshot.

    ``kind`` describes the authority that produced the identity (for example
    ``git-blob``, ``git-tree-path``, or ``working-tree-digest``).  Callers must
    never compare the opaque value without also comparing its kind.
    """

    kind: str
    value: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or not self.kind
            or not isinstance(self.value, str)
            or not self.value
        ):
            raise ValueError("snapshot identity kind and value must be non-empty")


@dataclass(frozen=True, slots=True)
class FileSnapshot(Generic[Space]):
    """Repository path paired with the identity of its exact content."""

    path: str
    identity: SnapshotIdentity
    line_count: int
    role: type[Space]

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("snapshot path must be non-empty")
        if not isinstance(self.identity, SnapshotIdentity):
            raise TypeError("snapshot identity must be a SnapshotIdentity")
        if type(self.line_count) is not int or self.line_count < 0:
            raise ValueError("snapshot line count must be non-negative")
        if not isinstance(self.role, type):
            raise TypeError("snapshot coordinate role must be a type")


@dataclass(frozen=True, slots=True, order=True)
class LineBoundary(Generic[Space]):
    """Zero-based boundary between lines in one coordinate role."""

    offset: int

    def __post_init__(self) -> None:
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("line boundary must be non-negative")


@dataclass(frozen=True, slots=True)
class LineSpan(Generic[Space]):
    """Zero-based half-open line span in one coordinate role."""

    start: LineBoundary[Space]
    end: LineBoundary[Space]

    def __post_init__(self) -> None:
        if self.start.offset > self.end.offset:
            raise ValueError("line span start must not exceed end")

    def __len__(self) -> int:
        return self.end.offset - self.start.offset



def require_same_snapshot(
    left: SnapshotDescriptor,
    right: SnapshotDescriptor,
) -> None:
    """Reject values whose paths or exact content identities differ."""
    if (
        left.path != right.path
        or left.identity != right.identity
        or left.role is not right.role
        or left.line_count != right.line_count
    ):
        raise ValueError("coordinate snapshots do not match")


def require_snapshot_role(
    snapshot: SnapshotDescriptor,
    role: type[Space],
) -> None:
    """Reject a snapshot whose runtime coordinate role is different."""
    if snapshot.role is not role:
        raise ValueError("snapshot has the wrong coordinate role")


def content_snapshot(
    path: str,
    lines: Sequence[bytes],
    *,
    space: type[Space],
) -> FileSnapshot[Space]:
    """Bind a borrowed line sequence to an exact storage-independent digest."""
    cached_digest = getattr(lines, "framed_content_sha256", None)
    digest_value = (
        cast(Callable[[], str], cached_digest)()
        if callable(cached_digest)
        else framed_content_sha256(lines)
    )
    cached_line_count = getattr(lines, "exact_line_count", None)
    line_count = (
        cast(Callable[[], int], cached_line_count)()
        if callable(cached_line_count)
        else len(lines)
    )
    return FileSnapshot(
        path,
        SnapshotIdentity("content-sha256", digest_value),
        line_count,
        space,
    )


def framed_content_sha256(lines: Iterable[bytes]) -> str:
    """Return the canonical line-framed digest used by content snapshots."""
    digest = hashlib.sha256()
    for line in lines:
        digest.update(len(line).to_bytes(8, "big"))
        digest.update(line)
    return digest.hexdigest()
