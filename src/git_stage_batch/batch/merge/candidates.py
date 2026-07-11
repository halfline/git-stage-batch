"""Merge candidate value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MergeResolutionDecision:
    """One selected merge ambiguity decision."""

    ambiguity_key: str
    choice_index: int
    choice_label: str


@dataclass(frozen=True)
class MergeResolution:
    """Concrete ambiguity decisions used to materialize a merge candidate."""

    decisions: Mapping[str, int]


@dataclass(frozen=True)
class MergeCandidate:
    """One complete target-level merge candidate."""

    ordinal: int
    count: int
    decisions: tuple[MergeResolutionDecision, ...]
    summary: str
    source_line_range: tuple[int, int] | None
    target_after_line: int | None
    target_before_line: int | None
    explanation: str
    ambiguity_target_line_range: tuple[int, int] | None = None

    @property
    def resolution(self) -> MergeResolution:
        return MergeResolution(
            {decision.ambiguity_key: decision.choice_index for decision in self.decisions}
        )


@dataclass(frozen=True)
class MergeCandidateSet:
    """Merge candidates for one target."""

    candidates: tuple[MergeCandidate, ...]
