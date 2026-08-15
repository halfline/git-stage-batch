"""Tests for review-aware batch line-selection translation."""

from __future__ import annotations

from git_stage_batch.core.models import (
    HunkHeader,
    LineLevelChange,
    RenderedBatchDisplay,
    ReviewActionGroup,
)
from git_stage_batch.data.file_review.batch_selection import (
    _complete_mixed_review_composite_dependencies,
)
from git_stage_batch.data.file_review.records import FileReviewAction


_APPLY = FileReviewAction.APPLY_FROM_BATCH.value
_RESET = FileReviewAction.RESET_FROM_BATCH.value


def _mixed_composite_display() -> RenderedBatchDisplay:
    return RenderedBatchDisplay(
        line_changes=LineLevelChange(
            path="file.c",
            header=HunkHeader(1, 1, 1, 1),
            lines=[],
        ),
        gutter_to_selection_id={},
        selection_id_to_gutter={},
        review_action_groups=(
            ReviewActionGroup((1, 2), (1, 2), (_APPLY, _RESET)),
            ReviewActionGroup((3, 5), (3, 5), (_APPLY, _RESET)),
            ReviewActionGroup((4, 6), (4, 6), (_RESET,)),
            ReviewActionGroup(
                (3, 4, 5, 6),
                (3, 4, 5, 6),
                (_APPLY, _RESET),
            ),
        ),
    )


def test_former_recommendation_completes_mixed_review_composite() -> None:
    """An all-other-actions command carries its newly provable composite."""
    completed = _complete_mixed_review_composite_dependencies(
        {1, 2},
        _mixed_composite_display(),
        FileReviewAction.APPLY_FROM_BATCH,
    )

    assert completed == {1, 2, 3, 4, 5, 6}


def test_partial_selection_does_not_complete_mixed_review_composite() -> None:
    """A normal partial selection must not acquire omitted review changes."""
    completed = _complete_mixed_review_composite_dependencies(
        {1},
        _mixed_composite_display(),
        FileReviewAction.APPLY_FROM_BATCH,
    )

    assert completed == {1}
