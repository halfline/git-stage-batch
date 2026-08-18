"""Tests for atomic transformed-selection projection authority."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.transformed_selection import (
    NoRollback,
    RollbackSelection,
    TransformedSelectionProjection,
)
from git_stage_batch.core.coordinates import (
    BaselineSpace,
    DiffNewSpace,
    DiffOldSpace,
    DisplayLineId,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotIdentity,
    WorktreeSpace,
)
from git_stage_batch.core.edit_plan import ReplacementEditPlan
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.core.selection_geometry import (
    ResolvedSelection,
    diff_view_identity,
    resolve_selection,
)


def _snapshot(role, identity: str) -> FileSnapshot:
    return FileSnapshot(
        "file.txt",
        SnapshotIdentity("test", identity),
        3,
        role,
    )


def _changes() -> LineLevelChange:
    return LineLevelChange(
        "file.txt",
        HunkHeader(1, 3, 1, 3),
        [
            LineEntry(1, "-", 1, None, text_bytes=b"old-first\n"),
            LineEntry(2, "+", None, 1, text_bytes=b"new-first\n"),
            LineEntry(None, " ", 2, 2, text_bytes=b"context\n"),
            LineEntry(3, "-", 3, None, text_bytes=b"old-last\n"),
            LineEntry(4, "+", None, 3, text_bytes=b"new-last\n"),
        ],
    )


def _selection(
    changes: LineLevelChange,
    ids: tuple[int, ...],
    *,
    old_snapshot: FileSnapshot,
    new_snapshot: FileSnapshot,
) -> ResolvedSelection:
    view = diff_view_identity(
        changes,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
    )
    return resolve_selection(
        changes,
        (DisplayLineId(display_id) for display_id in ids),
        view=view,
    )


def _edit(
    *,
    baseline_span: tuple[int, int] = (0, 1),
    worktree_span: tuple[int, int] = (0, 1),
    rewritten_span_count: int = 1,
):
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, "baseline"),
        worktree_snapshot=_snapshot(WorktreeSpace, "working"),
        baseline_span=LineSpan(
            LineBoundary(baseline_span[0]),
            LineBoundary(baseline_span[1]),
        ),
        worktree_span=LineSpan(
            LineBoundary(worktree_span[0]),
            LineBoundary(worktree_span[1]),
        ),
    )
    rewritten = _snapshot(RewrittenWorktreeSpace, "rewritten")
    return rewritten, plan.bind_result(
        rewritten,
        replacement_line_count=rewritten_span_count,
    )


def _original(ids: tuple[int, ...]) -> ResolvedSelection:
    return _selection(
        _changes(),
        ids,
        old_snapshot=_snapshot(DiffOldSpace, "baseline"),
        new_snapshot=_snapshot(DiffNewSpace, "working"),
    )


def _rewritten(ids: tuple[int, ...]) -> ResolvedSelection:
    return _selection(
        _changes(),
        ids,
        old_snapshot=_snapshot(DiffOldSpace, "baseline"),
        new_snapshot=_snapshot(DiffNewSpace, "rewritten"),
    )


def test_transformed_projection_binds_both_outputs_to_one_explicit_edit():
    rewritten_snapshot, edit = _edit()
    ownership = _rewritten((1, 2))

    projection = TransformedSelectionProjection(
        original_selection=_original((1, 2)),
        explicit_edit=edit,
        rewritten_snapshot=rewritten_snapshot,
        ownership_selection=ownership,
        rollback=RollbackSelection(_rewritten((2,))),
    )

    assert projection.ownership_selection == ownership


def test_transformed_projection_rejects_original_geometry_outside_edit():
    rewritten_snapshot, edit = _edit()

    with pytest.raises(ValueError, match="original selection old coordinates"):
        TransformedSelectionProjection(
            original_selection=_original((3, 4)),
            explicit_edit=edit,
            rewritten_snapshot=rewritten_snapshot,
            ownership_selection=_rewritten((1, 2)),
            rollback=NoRollback(),
        )


def test_transformed_projection_rejects_rewritten_geometry_outside_edit():
    rewritten_snapshot, edit = _edit()

    with pytest.raises(ValueError, match="ownership projection old coordinates"):
        TransformedSelectionProjection(
            original_selection=_original((1, 2)),
            explicit_edit=edit,
            rewritten_snapshot=rewritten_snapshot,
            ownership_selection=_rewritten((3, 4)),
            rollback=NoRollback(),
        )


def test_transformed_projection_rejects_unowned_rollback_rows():
    rewritten_snapshot, edit = _edit(
        baseline_span=(0, 3),
        worktree_span=(0, 3),
        rewritten_span_count=3,
    )

    with pytest.raises(ValueError, match="rollback selection exceeds"):
        TransformedSelectionProjection(
            original_selection=_original((1, 2)),
            explicit_edit=edit,
            rewritten_snapshot=rewritten_snapshot,
            ownership_selection=_rewritten((1, 2)),
            rollback=RollbackSelection(_rewritten((3, 4))),
        )
