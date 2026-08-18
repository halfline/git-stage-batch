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


class RewrittenWorktreeSpace:
    """Marker for coordinates after an explicit working-tree rewrite."""


class DiffOldSpace:
    """Marker for the old endpoint of a rendered diff."""


class DiffNewSpace:
    """Marker for the new endpoint of a rendered diff."""


class ReplacementOldSpace:
    """Marker for an original replacement's baseline-side span."""


class ReplacementNewSpace:
    """Marker for an original replacement's produced-side span."""


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


@dataclass(frozen=True, slots=True)
class SnapshotBoundary(Generic[Space]):
    """One boundary bound to an exact file snapshot."""

    snapshot: FileSnapshot[Space]
    boundary: LineBoundary[Space]

    def __post_init__(self) -> None:
        if self.boundary.offset > self.snapshot.line_count:
            raise ValueError("line boundary is outside its snapshot")


@dataclass(frozen=True, slots=True)
class SnapshotSpan(Generic[Space]):
    """One half-open line span bound to an exact file snapshot."""

    snapshot: FileSnapshot[Space]
    span: LineSpan[Space]

    def __post_init__(self) -> None:
        if self.span.end.offset > self.snapshot.line_count:
            raise ValueError("line span is outside its snapshot")


@dataclass(frozen=True, slots=True)
class HalfOpenRanges:
    """Compact normalized zero-based half-open ranges.

    Storage is proportional to the number of ranges, never the number of
    represented lines.  The type intentionally has no one-based compatibility
    behavior; adapters must opt into that conversion explicitly.
    """

    _ranges: tuple[tuple[int, int], ...] = ()
    _starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _count: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        normalized = _normalize_half_open_ranges(self._ranges)
        object.__setattr__(self, "_ranges", normalized)
        object.__setattr__(self, "_starts", tuple(start for start, _ in normalized))
        object.__setattr__(
            self,
            "_count",
            sum(end - start for start, end in normalized),
        )

    @classmethod
    def empty(cls) -> HalfOpenRanges:
        return cls()

    @classmethod
    def from_ranges(cls, ranges: Iterable[tuple[int, int]]) -> HalfOpenRanges:
        return cls(tuple(ranges))

    def ranges(self) -> tuple[tuple[int, int], ...]:
        return self._ranges

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __len__(self) -> int:
        return self._count

    def __contains__(self, offset: object) -> bool:
        if type(offset) is not int:
            return False
        index = bisect_right(self._starts, offset) - 1
        if index < 0:
            return False
        start, end = self._ranges[index]
        return start <= offset < end

    def __iter__(self) -> Iterator[int]:
        for start, end in self._ranges:
            yield from range(start, end)

    def union(self, other: HalfOpenRanges) -> HalfOpenRanges:
        return HalfOpenRanges(self._ranges + other._ranges)


@dataclass(frozen=True, slots=True)
class SnapshotSpans(Generic[Space]):
    """Compact spans whose coordinate space is one exact file snapshot."""

    snapshot: FileSnapshot[Space]
    ranges: HalfOpenRanges

    def __post_init__(self) -> None:
        for start, end in self.ranges.ranges():
            if end > self.snapshot.line_count:
                raise ValueError("span is outside its snapshot")


@dataclass(frozen=True, slots=True)
class DisplayLineId:
    """Opaque selection handle assigned by one rendered diff view."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError("display line ID must be positive")


class SnapshotDescriptor(Protocol):
    """Read-only snapshot identity accepted across coordinate roles."""

    @property
    def path(self) -> str: ...

    @property
    def identity(self) -> SnapshotIdentity: ...

    @property
    def line_count(self) -> int: ...

    @property
    def role(self) -> type[object]: ...


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


def one_based_inclusive_to_half_open(start: int, end: int) -> tuple[int, int]:
    """Convert one-based inclusive Git coordinates to a domain range."""
    if type(start) is not int or type(end) is not int or start <= 0 or end < start:
        raise ValueError("invalid one-based inclusive range")
    return start - 1, end


def snapshot_as_role(
    snapshot: SnapshotDescriptor,
    role: type[Space],
) -> FileSnapshot[Space]:
    """Rebind identical content to an explicit coordinate endpoint role."""
    return FileSnapshot(
        snapshot.path,
        snapshot.identity,
        snapshot.line_count,
        role,
    )


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


def _normalize_half_open_ranges(
    ranges: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    checked_ranges: list[tuple[int, int]] = []
    for start, end in ranges:
        if type(start) is not int or type(end) is not int:
            raise ValueError("half-open range boundaries must be integers")
        checked_ranges.append((start, end))
    normalized: list[tuple[int, int]] = []
    for start, end in sorted(checked_ranges):
        if start < 0 or end < start:
            raise ValueError("half-open ranges must be non-negative and ordered")
        if start == end:
            continue
        if normalized and start <= normalized[-1][1]:
            previous_start, previous_end = normalized[-1]
            normalized[-1] = (previous_start, max(previous_end, end))
        else:
            normalized.append((start, end))
    return tuple(normalized)
