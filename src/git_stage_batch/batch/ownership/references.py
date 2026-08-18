"""Baseline boundary reference metadata for batch ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ...core.coordinates import (
    BaselineSpace,
    FileSnapshot,
    LineBoundary,
    SnapshotBoundary,
    require_snapshot_role,
)
from ...utils.git_object_io import create_git_blob
from .metadata_blobs import read_metadata_blob as _metadata_blob_content
from .metadata_types import BaselineReferenceMetadata


@dataclass(frozen=True, slots=True)
class UnknownBoundary:
    """No evidence exists for this side of a baseline boundary."""


@dataclass(frozen=True, slots=True)
class KnownBoundary:
    """A known baseline boundary side and its optional identity bytes."""

    line: int | None
    content: bytes | None = None

    def __post_init__(self) -> None:
        if self.line is not None and (
            type(self.line) is not int or self.line <= 0
        ):
            raise ValueError("baseline boundary line must be positive")
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("baseline boundary content must be bytes")
        if self.line is None and self.content is not None:
            raise ValueError("SOF or EOF boundary cannot carry line content")


BoundaryEvidence = Union[UnknownBoundary, KnownBoundary]


@dataclass(frozen=True, slots=True)
class BoundBoundary:
    """One known boundary bound to the exact baseline that authorizes it."""

    position: SnapshotBoundary[BaselineSpace]
    content: bytes | None = None

    def __post_init__(self) -> None:
        require_snapshot_role(self.position.snapshot, BaselineSpace)
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("bound baseline boundary content must be bytes")


BoundBoundaryEvidence = Union[UnknownBoundary, BoundBoundary]


@dataclass(frozen=True, slots=True)
class BoundBaselineReference:
    """Canonical baseline reference with explicit SOF/EOF positions."""

    after: BoundBoundaryEvidence
    before: BoundBoundaryEvidence


@dataclass(frozen=True, slots=True, init=False)
class BaselineReference:
    """Baseline-side coordinate and optional boundary identity.

    The line numbers are old-file coordinates from the diff that produced the
    selection. Byte payloads, when present, let a later merge prove the target
    still has the same local boundary before applying a baseline coordinate.
    """

    after: BoundaryEvidence
    before: BoundaryEvidence

    def __init__(
        self,
        after_line: int | None = None,
        after_content: bytes | None = None,
        has_after_line: bool = True,
        before_line: int | None = None,
        before_content: bytes | None = None,
        has_before_line: bool = False,
        *,
        after: BoundaryEvidence | None = None,
        before: BoundaryEvidence | None = None,
    ) -> None:
        if after is not None and (
            after_line is not None
            or after_content is not None
            or not has_after_line
        ):
            raise ValueError("provide after evidence or legacy after fields")
        if before is not None and (
            before_line is not None
            or before_content is not None
            or has_before_line
        ):
            raise ValueError("provide before evidence or legacy before fields")
        if after is not None and not isinstance(
            after,
            (UnknownBoundary, KnownBoundary),
        ):
            raise TypeError("after evidence must be a boundary variant")
        if before is not None and not isinstance(
            before,
            (UnknownBoundary, KnownBoundary),
        ):
            raise TypeError("before evidence must be a boundary variant")
        object.__setattr__(
            self,
            "after",
            after
            if after is not None
            else (
                KnownBoundary(after_line, after_content)
                if has_after_line
                else UnknownBoundary()
            ),
        )
        object.__setattr__(
            self,
            "before",
            before
            if before is not None
            else (
                KnownBoundary(before_line, before_content)
                if has_before_line
                else UnknownBoundary()
            ),
        )

    @property
    def after_line(self) -> int | None:
        return self.after.line if isinstance(self.after, KnownBoundary) else None

    @property
    def after_content(self) -> bytes | None:
        return (
            self.after.content
            if isinstance(self.after, KnownBoundary)
            else None
        )

    @property
    def has_after_line(self) -> bool:
        return isinstance(self.after, KnownBoundary)

    @property
    def before_line(self) -> int | None:
        return self.before.line if isinstance(self.before, KnownBoundary) else None

    @property
    def before_content(self) -> bytes | None:
        return (
            self.before.content
            if isinstance(self.before, KnownBoundary)
            else None
        )

    @property
    def has_before_line(self) -> bool:
        return isinstance(self.before, KnownBoundary)

    def bind(
        self,
        snapshot: FileSnapshot[BaselineSpace],
    ) -> BoundBaselineReference:
        """Resolve legacy line-side fields into snapshot-bound boundaries.

        A known missing prior line is SOF (offset 0); a known missing
        following line is EOF (offset ``line_count``).  Unknown evidence stays
        an explicit variant and can no longer be confused with either edge.
        """
        require_snapshot_role(snapshot, BaselineSpace)

        def bind_after() -> BoundBoundaryEvidence:
            if not isinstance(self.after, KnownBoundary):
                return UnknownBoundary()
            offset = self.after.line or 0
            if offset > snapshot.line_count:
                raise ValueError("baseline after-boundary is outside its snapshot")
            return BoundBoundary(
                SnapshotBoundary(snapshot, LineBoundary(offset)),
                self.after.content,
            )

        def bind_before() -> BoundBoundaryEvidence:
            if not isinstance(self.before, KnownBoundary):
                return UnknownBoundary()
            offset = (
                snapshot.line_count
                if self.before.line is None
                else self.before.line - 1
            )
            if offset > snapshot.line_count and self.before.line is not None:
                raise ValueError("baseline before-boundary is outside its snapshot")
            return BoundBoundary(
                SnapshotBoundary(snapshot, LineBoundary(offset)),
                self.before.content,
            )

        return BoundBaselineReference(bind_after(), bind_before())

    def to_dict(self) -> BaselineReferenceMetadata:
        """Serialize to metadata dictionary."""
        data: BaselineReferenceMetadata = {}
        if isinstance(self.after, KnownBoundary):
            data["after_line"] = self.after.line
            if self.after.content is not None:
                data["after_blob"] = create_git_blob([self.after.content])
        if isinstance(self.before, KnownBoundary):
            data["before_line"] = self.before.line
            if self.before.content is not None:
                data["before_blob"] = create_git_blob([self.before.content])
        return data

    @classmethod
    def from_dict(
        cls,
        data: BaselineReferenceMetadata,
        blob_contents: dict[str, bytes] | None = None,
    ) -> BaselineReference:
        """Deserialize from metadata dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Baseline reference metadata must be a dictionary")

        after_blob = data.get("after_blob")
        before_blob = data.get("before_blob")
        after_content = _metadata_blob_content(after_blob, blob_contents)
        before_content = _metadata_blob_content(before_blob, blob_contents)
        return cls(
            after=(
                KnownBoundary(data.get("after_line"), after_content)
                if "after_line" in data
                else UnknownBoundary()
            ),
            before=(
                KnownBoundary(data.get("before_line"), before_content)
                if "before_line" in data
                else UnknownBoundary()
            ),
        )
