"""Tests for merge candidate result value objects."""

import pytest

from git_stage_batch.batch.merge.candidates import (
    MergeCandidate,
    MergeCandidateSet,
    MergeCandidateSetOutcome,
)
from git_stage_batch.batch.merge.coordinate_strategy import (
    CoordinateStrategyChoice,
)


def _candidate() -> MergeCandidate:
    return MergeCandidate(
        ordinal=1,
        count=1,
        decisions=(),
        summary="review this merge",
        source_line_range=None,
        target_after_line=None,
        target_before_line=None,
        explanation="candidate result",
    )


@pytest.mark.parametrize(
    "outcome",
    [
        MergeCandidateSetOutcome.REFUSED,
        MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED,
    ],
)
def test_nonreview_outcomes_reject_candidates(outcome):
    """No-review outcomes must not carry contradictory candidates."""
    with pytest.raises(ValueError, match="review-required"):
        MergeCandidateSet((_candidate(),), outcome)


def test_review_outcome_requires_candidates():
    """A review-required result must include at least one concrete choice."""
    with pytest.raises(ValueError, match="review-required"):
        MergeCandidateSet((), MergeCandidateSetOutcome.REVIEW_REQUIRED)


def test_candidate_result_factories_construct_valid_outcomes():
    """Named constructors should expose the complete result state."""
    candidate = _candidate()

    refused = MergeCandidateSet.refused()
    ordinary = MergeCandidateSet.ordinary_merge()
    review = MergeCandidateSet.review_required((candidate,))

    assert refused == MergeCandidateSet(
        (),
        MergeCandidateSetOutcome.REFUSED,
    )
    assert ordinary == MergeCandidateSet(
        (),
        MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED,
    )
    assert review == MergeCandidateSet(
        (candidate,),
        MergeCandidateSetOutcome.REVIEW_REQUIRED,
    )


def test_coordinate_strategy_is_not_implicitly_an_integer():
    """Strategy decisions should serialize explicitly through their values."""
    assert not isinstance(CoordinateStrategyChoice.STRUCTURAL, int)
    assert CoordinateStrategyChoice.STRUCTURAL.value == 1
