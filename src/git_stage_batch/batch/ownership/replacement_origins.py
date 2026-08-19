"""Explicit replacement-origin input variants."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Union

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
