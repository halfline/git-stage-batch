"""Tests for explicit saved/live replacement geometry."""

import pytest

from git_stage_batch.batch.replacement_alternatives import (
    ExplicitReplacementAlternatives,
    ExplicitReplacementParent,
    ReplacementAlternativeOwnership,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.resolved_replacement_alternatives import (
    InvalidReplacementAlternatives,
    ResolvedReplacementAlternative,
)
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


def _replacement_geometry():
    baseline = content_snapshot(
        "file.txt",
        [b"old\n", b"shared\n", b"tail\n"],
        space=BaselineSpace,
    )
    worktree = content_snapshot(
        "file.txt",
        [b"current\n", b"shared\n", b"tail\n"],
        space=WorktreeSpace,
    )
    rewritten = content_snapshot(
        "file.txt",
        [b"saved\n", b"live\n", b"shared\n", b"tail\n"],
        space=RewrittenWorktreeSpace,
    )
    plan = ReplacementEditPlan(
        path="file.txt",
        baseline_snapshot=baseline,
        worktree_snapshot=worktree,
        baseline_span=LineSpan(LineBoundary(0), LineBoundary(1)),
        worktree_span=LineSpan(LineBoundary(0), LineBoundary(1)),
    )
    edit = plan.bind_result(rewritten, replacement_line_count=2)
    parent = ExplicitReplacementParent(
        baseline=SnapshotSpan(
            baseline,
            LineSpan(LineBoundary(0), LineBoundary(1)),
        ),
        worktree=SnapshotSpan(
            worktree,
            LineSpan(LineBoundary(0), LineBoundary(1)),
        ),
    )
    return edit, rewritten, parent


def test_alternatives_bind_saved_live_and_parent_spans() -> None:
    """Saved/live roles remain distinct even when live extends past the edit."""
    edit, rewritten, parent = _replacement_geometry()
    alternatives = ExplicitReplacementAlternatives(
        edit=edit,
        saved=SnapshotSpan(
            rewritten,
            LineSpan(LineBoundary(0), LineBoundary(1)),
        ),
        live=SnapshotSpan(
            rewritten,
            LineSpan(LineBoundary(1), LineBoundary(3)),
        ),
        parent=parent,
        ownership_scope=ReplacementAlternativeOwnership.TRANSLATED_SELECTION,
    )

    assert alternatives.saved_range == (1, 1)
    assert alternatives.live_range == (2, 3)
    assert alternatives.boundary_search_span.span == LineSpan(
        LineBoundary(0),
        LineBoundary(3),
    )
    assert parent.as_line_run().old_start == 1
    assert parent.as_line_run().new_start == 1


def test_alternatives_reject_nonadjacent_live_span() -> None:
    """A live predecessor must immediately follow its saved snapshot."""
    edit, rewritten, parent = _replacement_geometry()

    with pytest.raises(ValueError, match="not adjacent"):
        ExplicitReplacementAlternatives(
            edit=edit,
            saved=SnapshotSpan(
                rewritten,
                LineSpan(LineBoundary(0), LineBoundary(1)),
            ),
            live=SnapshotSpan(
                rewritten,
                LineSpan(LineBoundary(2), LineBoundary(3)),
            ),
            parent=parent,
            ownership_scope=ReplacementAlternativeOwnership.TRANSLATED_SELECTION,
        )


def test_presence_only_alternative_rejects_tracked_parent() -> None:
    """Presence-only additions cannot also claim a baseline replacement parent."""
    edit, rewritten, parent = _replacement_geometry()

    with pytest.raises(ValueError, match="tracked parent"):
        ExplicitReplacementAlternatives(
            edit=edit,
            saved=SnapshotSpan(
                rewritten,
                LineSpan(LineBoundary(0), LineBoundary(1)),
            ),
            live=SnapshotSpan(
                rewritten,
                LineSpan(LineBoundary(1), LineBoundary(2)),
            ),
            parent=parent,
            ownership_scope=ReplacementAlternativeOwnership.EXACT_SAVED_SPAN,
        )


def test_alternatives_reject_parent_from_different_edit_geometry() -> None:
    """A parent cannot silently mix spans from another replacement edit."""
    edit, rewritten, parent = _replacement_geometry()
    mismatched_parent = ExplicitReplacementParent(
        baseline=SnapshotSpan(
            parent.baseline.snapshot,
            LineSpan(LineBoundary(1), LineBoundary(2)),
        ),
        worktree=parent.worktree,
    )

    with pytest.raises(ValueError, match="extend the edit equally"):
        ExplicitReplacementAlternatives(
            edit=edit,
            saved=SnapshotSpan(
                rewritten,
                LineSpan(LineBoundary(0), LineBoundary(1)),
            ),
            live=None,
            parent=mismatched_parent,
            ownership_scope=ReplacementAlternativeOwnership.TRANSLATED_SELECTION,
        )


def test_batch_ownership_resolves_persisted_replacement_alternative() -> None:
    """Indirect metadata becomes one typed saved/live value before replay."""
    claim = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"live one\n", b"live two\n"],
        source_alternative=True,
    )
    resolved = BatchOwnership.from_presence_lines(
        ["2-3"],
        [claim],
        replacement_units=[ReplacementUnit(["2-3"], [0])],
    ).resolve()
    live_span: LineSpan[BatchSourceSpace] = LineSpan(
        LineBoundary(3),
        LineBoundary(5),
    )

    assert resolved.replacement_alternatives == (
        ResolvedReplacementAlternative(
            unit_index=0,
            deletion_index=0,
            saved=LineSpan(LineBoundary(1), LineBoundary(3)),
            live_payload=(live_span,),
            live_envelope=live_span,
            absence_claim=claim,
        ),
    )


def test_resolved_alternatives_are_ordered_by_live_source_span() -> None:
    """Replay can binary-search alternatives independently of deletion order."""
    later_claim = AbsenceClaim(
        anchor_line=6,
        content_lines=[b"later live\n"],
        source_alternative=True,
    )
    earlier_claim = AbsenceClaim(
        anchor_line=2,
        content_lines=[b"earlier live\n"],
        source_alternative=True,
    )
    alternatives = (
        BatchOwnership.from_presence_lines(
            ["2", "6"],
            [later_claim, earlier_claim],
            replacement_units=[
                ReplacementUnit(["6"], [0]),
                ReplacementUnit(["2"], [1]),
            ],
        )
        .resolve()
        .replacement_alternatives
    )

    assert [alternative.deletion_index for alternative in alternatives] == [1, 0]
    assert [alternative.live_envelope.start.offset for alternative in alternatives] == [
        2,
        6,
    ]


def test_batch_ownership_resolves_nested_live_alternative_payload() -> None:
    """A later live alternative can split an enclosing predecessor payload."""
    outer_claim = AbsenceClaim(
        content_lines=[b"outer one\n", b"inner saved\n", b"outer two\n"],
        source_alternative=True,
    )
    inner_claim = AbsenceClaim(
        content_lines=[b"inner live\n"],
        source_alternative=True,
    )
    alternatives = (
        BatchOwnership.from_presence_lines(
            ["1-2", "4"],
            [outer_claim, inner_claim],
            replacement_units=[
                ReplacementUnit(["1-2"], [0]),
                ReplacementUnit(["4"], [1]),
            ],
        )
        .resolve()
        .replacement_alternatives
    )

    assert alternatives[0].live_payload == (
        LineSpan(LineBoundary(2), LineBoundary(4)),
        LineSpan(LineBoundary(5), LineBoundary(6)),
    )
    assert alternatives[0].live_envelope == LineSpan(
        LineBoundary(2),
        LineBoundary(6),
    )
    assert alternatives[1].live_payload == (LineSpan(LineBoundary(4), LineBoundary(5)),)


def test_nested_alternative_at_payload_end_extends_parent_envelope() -> None:
    """A child at the last parent line still extends its stored live region."""
    outer_claim = AbsenceClaim(
        content_lines=[b"outer\n", b"inner saved\n"],
        source_alternative=True,
    )
    inner_claim = AbsenceClaim(
        content_lines=[b"inner live\n"],
        source_alternative=True,
    )
    alternatives = (
        BatchOwnership.from_presence_lines(
            ["1-2", "4"],
            [outer_claim, inner_claim],
            replacement_units=[
                ReplacementUnit(["1-2"], [0]),
                ReplacementUnit(["4"], [1]),
            ],
        )
        .resolve()
        .replacement_alternatives
    )

    assert alternatives[0].live_payload == (
        LineSpan(LineBoundary(2), LineBoundary(4)),
    )
    assert alternatives[0].live_envelope == LineSpan(
        LineBoundary(2),
        LineBoundary(5),
    )


def test_batch_ownership_rejects_uncoupled_persisted_alternative() -> None:
    """Every explicit old side belongs to exactly one replacement unit."""
    with pytest.raises(InvalidReplacementAlternatives, match="not coupled"):
        BatchOwnership.from_presence_lines(
            ["1"],
            [AbsenceClaim(content_lines=[b"old\n"], source_alternative=True)],
        ).resolve()
