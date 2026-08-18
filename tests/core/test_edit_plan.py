"""Tests for snapshot-bound explicit replacement transforms."""

import pytest

from git_stage_batch.core.coordinates import (
    BaselineSpace,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotBoundary,
    SnapshotIdentity,
    SnapshotSpan,
    WorktreeSpace,
)
from git_stage_batch.core.edit_plan import ReplacementEditPlan


def _snapshot(role, identity: str, count: int):
    return FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", identity),
        count,
        role,
    )


def test_applied_replacement_is_an_exact_snapshot_bound_transform():
    worktree = _snapshot(WorktreeSpace, "before", 5)
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, "baseline", 5),
        worktree_snapshot=worktree,
        baseline_span=LineSpan(LineBoundary(1), LineBoundary(3)),
        worktree_span=LineSpan(LineBoundary(1), LineBoundary(3)),
    )
    rewritten = _snapshot(RewrittenWorktreeSpace, "after", 6)

    transform = plan.bind_result(rewritten, replacement_line_count=3)

    assert transform.translate_span(
        SnapshotSpan(worktree, plan.worktree_span)
    ) == SnapshotSpan(
        rewritten,
        LineSpan(LineBoundary(1), LineBoundary(4)),
    )
    assert transform.translate_boundary(
        SnapshotBoundary(worktree, LineBoundary(4))
    ) == SnapshotBoundary(rewritten, LineBoundary(5))
    assert transform.translate_boundary(
        SnapshotBoundary(worktree, LineBoundary(2))
    ) is None


def test_applied_replacement_rejects_unrelated_result_extent():
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, "baseline", 2),
        worktree_snapshot=_snapshot(WorktreeSpace, "before", 2),
        baseline_span=LineSpan(LineBoundary(0), LineBoundary(1)),
        worktree_span=LineSpan(LineBoundary(0), LineBoundary(1)),
    )

    with pytest.raises(ValueError, match="extent"):
        plan.bind_result(
            _snapshot(RewrittenWorktreeSpace, "wrong", 99),
            replacement_line_count=1,
        )


def test_inserted_content_does_not_give_one_boundary_two_authoritative_sides():
    worktree = _snapshot(WorktreeSpace, "before", 3)
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, "baseline", 3),
        worktree_snapshot=worktree,
        baseline_span=LineSpan(LineBoundary(1), LineBoundary(1)),
        worktree_span=LineSpan(LineBoundary(1), LineBoundary(1)),
    )
    rewritten = _snapshot(RewrittenWorktreeSpace, "after", 5)
    transform = plan.bind_result(rewritten, replacement_line_count=2)

    assert transform.translate_boundary(
        SnapshotBoundary(worktree, LineBoundary(1))
    ) is None
    assert transform.translate_span(
        SnapshotSpan(
            worktree,
            LineSpan(LineBoundary(0), LineBoundary(1)),
        )
    ) == SnapshotSpan(
        rewritten,
        LineSpan(LineBoundary(0), LineBoundary(1)),
    )
    assert transform.translate_span(
        SnapshotSpan(
            worktree,
            LineSpan(LineBoundary(1), LineBoundary(3)),
        )
    ) == SnapshotSpan(
        rewritten,
        LineSpan(LineBoundary(3), LineBoundary(5)),
    )


@pytest.mark.parametrize("replacement_line_count", [-1, True])
def test_applied_replacement_rejects_invalid_replacement_line_count(
    replacement_line_count: object,
) -> None:
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, "baseline", 0),
        worktree_snapshot=_snapshot(WorktreeSpace, "before", 0),
        baseline_span=LineSpan(LineBoundary(0), LineBoundary(0)),
        worktree_span=LineSpan(LineBoundary(0), LineBoundary(0)),
    )

    with pytest.raises(ValueError, match="line count"):
        plan.bind_result(
            _snapshot(RewrittenWorktreeSpace, "after", 0),
            replacement_line_count=replacement_line_count,  # type: ignore[arg-type]
        )
