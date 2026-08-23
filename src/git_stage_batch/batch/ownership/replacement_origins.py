"""Explicit replacement-origin input variants."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar, Union

from ...core.coordinates import (
    BatchSourceSpace,
    FileSnapshot,
    SnapshotSpan,
)

from .replacement_line_runs import ReplacementLineRun


@dataclass(frozen=True, slots=True)
class NoReplacementOrigin:
    """Replacement units have only legacy local-hunk origin evidence."""


@dataclass(frozen=True, slots=True)
class SameStreamReplacementOrigin:
    """Replacement runs are also their authoritative origin runs."""

    source_lines: Sequence[bytes]


@dataclass(frozen=True, slots=True)
class ProjectedReplacementOrigin:
    """Replacement units project through an independent origin run stream."""

    runs: Iterable[ReplacementLineRun]
    source_lines: Sequence[bytes]


ReplacementOrigin = Union[
    NoReplacementOrigin,
    SameStreamReplacementOrigin,
    ProjectedReplacementOrigin,
]


OriginSourceSpace = TypeVar("OriginSourceSpace")


class ReplacementOriginSourceProjection(Protocol[OriginSourceSpace]):
    """Snapshot-bound projection from live replacement to batch source."""

    @property
    def source_snapshot(self) -> FileSnapshot[OriginSourceSpace]: ...

    @property
    def target_snapshot(self) -> FileSnapshot[BatchSourceSpace]: ...

    def translate_span(
        self,
        span: SnapshotSpan[OriginSourceSpace],
    ) -> SnapshotSpan[BatchSourceSpace] | None: ...
