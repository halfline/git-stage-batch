"""Merge candidate value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


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


class MergeCandidateSetOutcome(Enum):
    """Outcome represented by one merge-candidate discovery result."""

    REFUSED = "refused"
    ORDINARY_MERGE_SUCCEEDED = "ordinary-merge-succeeded"
    REVIEW_REQUIRED = "review-required"


@dataclass(frozen=True)
class MergeCandidateSet:
    """Validated merge-candidate discovery result for one target."""

    candidates: tuple[MergeCandidate, ...]
    outcome: MergeCandidateSetOutcome = MergeCandidateSetOutcome.REFUSED

    def __post_init__(self) -> None:
        review_required = (
            self.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED
        )
        if bool(self.candidates) != review_required:
            raise ValueError(
                "review-required results must contain candidates and "
                "other results must not"
            )

    @classmethod
    def refused(cls) -> MergeCandidateSet:
        """Return a hard refusal with no enumerable review choices."""
        return cls((), MergeCandidateSetOutcome.REFUSED)

    @classmethod
    def ordinary_merge(cls) -> MergeCandidateSet:
        """Return an ordinary merge that needs no candidate review."""
        return cls((), MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED)

    @classmethod
    def review_required(
        cls,
        candidates: tuple[MergeCandidate, ...],
    ) -> MergeCandidateSet:
        """Return a nonempty set of reviewed merge choices."""
        return cls(candidates, MergeCandidateSetOutcome.REVIEW_REQUIRED)
