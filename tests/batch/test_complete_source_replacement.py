"""Tests for complete saved/live source replacement snapshots."""

from git_stage_batch.batch.complete_source_replacement import (
    materialize_untracked_source_replacement,
    resolve_complete_source_replacement,
)
from git_stage_batch.batch.file_state import SourceBoundOwnership
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.replacement_alternatives import (
    ExplicitReplacementAlternatives,
    ReplacementAlternativeOwnership,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    WorktreeSpace,
    content_snapshot,
)
from git_stage_batch.core.edit_plan import ReplacementEditPlan


def _complete_ownership(
    path: str,
    source_lines: LineBuffer,
    *,
    saved_line_count: int,
) -> SourceBoundOwnership:
    live_lines = source_lines[saved_line_count:]
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{saved_line_count}"],
        [
            AbsenceClaim(
                content_lines=live_lines,
                source_alternative=True,
                complete_file_pair=True,
            )
        ],
        replacement_units=[ReplacementUnit([f"1-{saved_line_count}"], [0])],
    )
    return SourceBoundOwnership(
        content_snapshot(path, source_lines, space=BatchSourceSpace),
        ownership,
    )


def test_resolve_complete_source_replacement_binds_both_file_snapshots() -> None:
    """The strict persisted shape becomes two snapshot-bound spans."""
    with LineBuffer.from_chunks(
        [b"saved one\n", b"saved two\n", b"live one\n"]
    ) as source_lines:
        resolved = resolve_complete_source_replacement(
            source_lines,
            _complete_ownership(
                "file.txt",
                source_lines,
                saved_line_count=2,
            ),
        )

        assert resolved is not None
        assert resolved.saved.span == LineSpan(
            LineBoundary(0),
            LineBoundary(2),
        )
        assert resolved.live.span == LineSpan(
            LineBoundary(2),
            LineBoundary(3),
        )


def test_materialize_untracked_replacement_stores_complete_file_snapshots() -> None:
    """A narrow first replacement retains complete saved and live files."""
    path = "file.txt"
    target = (b"prefix\n", b"new\n", b"suffix\n")
    predecessor = (b"prefix\n", b"old\n", b"suffix\n")
    rewritten = (b"prefix\n", b"new\n", b"old\n", b"suffix\n")
    baseline_snapshot = content_snapshot(path, (), space=BaselineSpace)
    worktree_snapshot = content_snapshot(path, target, space=WorktreeSpace)
    rewritten_snapshot = content_snapshot(
        path,
        rewritten,
        space=RewrittenWorktreeSpace,
    )
    edit = ReplacementEditPlan(
        path=path,
        baseline_snapshot=baseline_snapshot,
        worktree_snapshot=worktree_snapshot,
        baseline_span=LineSpan(LineBoundary(0), LineBoundary(0)),
        worktree_span=LineSpan(LineBoundary(1), LineBoundary(2)),
    ).bind_result(rewritten_snapshot, replacement_line_count=2)
    alternatives = ExplicitReplacementAlternatives(
        edit=edit,
        saved=SnapshotSpan(
            rewritten_snapshot,
            LineSpan(LineBoundary(1), LineBoundary(2)),
        ),
        live=SnapshotSpan(
            rewritten_snapshot,
            LineSpan(LineBoundary(2), LineBoundary(3)),
        ),
        parent=None,
        ownership_scope=ReplacementAlternativeOwnership.UNTRACKED_SOURCE,
    )

    with materialize_untracked_source_replacement(
        rewritten,
        alternatives,
    ) as materialized:
        assert tuple(materialized.source_buffer) == (*target, *predecessor)
        assert materialized.bound_ownership.value.presence_line_set().ranges() == (
            (1, len(target)),
        )
        assert (
            resolve_complete_source_replacement(
                materialized.source_buffer,
                materialized.bound_ownership,
            )
            is not None
        )
