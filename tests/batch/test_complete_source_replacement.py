"""Tests for complete saved/live source replacement snapshots."""

from git_stage_batch.batch.complete_source_replacement import (
    changes_from_complete_source_replacement,
    materialize_untracked_source_replacement,
    promote_untracked_presence_to_complete_source_replacement,
    refresh_complete_source_replacement,
    resolve_complete_source_replacement,
)
from git_stage_batch.batch.file_state import SourceBoundOwnership
from git_stage_batch.batch.merge.merge import (
    merge_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.claims import presence_claims_from_source_lines
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.batch.ownership.unit_rebuild import (
    rebuild_ownership_from_units,
)
from git_stage_batch.batch.ownership.units import (
    build_ownership_units_from_batch_source_lines,
)
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


def test_resolve_unmarked_complete_pair_from_empty_baseline_metadata() -> None:
    """The exact legacy empty-file shape retains whole-file replay behavior."""
    empty_file = BaselineReference(
        after_line=None,
        before_line=None,
        has_before_line=True,
    )
    with LineBuffer.from_chunks([b"saved\n", b"live\n"]) as source_lines:
        ownership = BatchOwnership(
            presence_claims=presence_claims_from_source_lines(
                [1],
                {1: empty_file},
            ),
            deletions=[
                AbsenceClaim(
                    content_lines=[b"live\n"],
                    baseline_reference=empty_file,
                    source_alternative=True,
                )
            ],
            replacement_units=[ReplacementUnit(["1"], [0])],
        )

        resolved = resolve_complete_source_replacement(
            source_lines,
            SourceBoundOwnership(
                content_snapshot(
                    "file.txt",
                    source_lines,
                    space=BatchSourceSpace,
                ),
                ownership,
            ),
        )

        assert resolved is None
        changes = changes_from_complete_source_replacement(
            source_lines,
            ownership,
            unmarked_target_lines=[b"live\n"],
        )
        assert changes is not None
        assert tuple(changes.source_lines) == (b"saved\n",)
        assert tuple(changes.live_lines) == (b"live\n",)


def test_unmarked_source_alternative_without_empty_baseline_is_not_complete() -> None:
    """An ordinary unmarked region does not become a whole-file pair by shape."""
    with LineBuffer.from_chunks([b"saved\n", b"live\n"]) as source_lines:
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            [AbsenceClaim(content_lines=[b"live\n"], source_alternative=True)],
            replacement_units=[ReplacementUnit(["1"], [0])],
        )

        assert (
            resolve_complete_source_replacement(
                source_lines,
                SourceBoundOwnership(
                    content_snapshot(
                        "file.txt",
                        source_lines,
                        space=BatchSourceSpace,
                    ),
                    ownership,
                ),
            )
            is None
        )


def test_complete_source_replacement_owns_only_changed_lines() -> None:
    """Complete snapshots preserve unrelated worktree edits during replay."""
    saved_file = (b"# Heading\n", b"anchor\n", b"new value\n", b"tail\n")
    live_file = (b"# Heading\n", b"anchor\n", b"old value\n", b"tail\n")
    working_file = (
        b"# Revised heading\n",
        b"anchor\n",
        b"old value\n",
        b"tail\n",
    )
    with LineBuffer.from_chunks((*saved_file, *live_file)) as source_lines:
        complete_ownership = _complete_ownership(
            "file.txt",
            source_lines,
            saved_line_count=len(saved_file),
        ).value

        changes = changes_from_complete_source_replacement(
            source_lines,
            complete_ownership,
        )

        assert changes is not None
        assert tuple(changes.source_lines) == saved_file
        assert changes.ownership.presence_line_set().ranges() == ((3, 3),)
        assert len(changes.ownership.deletions) == 1
        assert tuple(changes.ownership.deletions[0].content_lines) == (b"old value\n",)
        assert not changes.ownership.deletions[0].source_alternative

        with merge_batch_from_line_sequences_as_buffer(
            source_lines,
            complete_ownership,
            working_file,
        ) as merged:
            assert tuple(merged) == (
                b"# Revised heading\n",
                b"anchor\n",
                b"new value\n",
                b"tail\n",
            )


def test_complete_source_one_sided_changes_remain_atomic() -> None:
    """A displayed insertion or deletion still rebuilds the complete pair."""
    cases = (
        (
            (b"head\n", b"new\n", b"tail\n"),
            (b"head\n", b"tail\n"),
        ),
        (
            (b"head\n", b"tail\n"),
            (b"head\n", b"old\n", b"tail\n"),
        ),
    )
    for saved_file, live_file in cases:
        with LineBuffer.from_chunks((*saved_file, *live_file)) as source_lines:
            ownership = _complete_ownership(
                "file.txt",
                source_lines,
                saved_line_count=len(saved_file),
            ).value

            units = build_ownership_units_from_batch_source_lines(
                ownership,
                source_lines,
            )

            assert len(units) == 1
            assert units[0].is_atomic
            rebuilt = rebuild_ownership_from_units(units)
            assert (
                resolve_complete_source_replacement(
                    source_lines,
                    SourceBoundOwnership(
                        content_snapshot(
                            "file.txt",
                            source_lines,
                            space=BatchSourceSpace,
                        ),
                        rebuilt,
                    ),
                )
                is not None
            )
            with merge_batch_from_line_sequences_as_buffer(
                source_lines,
                rebuilt,
                live_file,
            ) as merged:
                assert tuple(merged) == saved_file


def test_identical_complete_files_apply_as_a_noop() -> None:
    """An unchanged saved/live pair owns no visible lines."""
    file_lines = (b"head\n", b"tail\n")
    with LineBuffer.from_chunks((*file_lines, *file_lines)) as source_lines:
        ownership = _complete_ownership(
            "file.txt",
            source_lines,
            saved_line_count=len(file_lines),
        ).value

        changes = changes_from_complete_source_replacement(
            source_lines,
            ownership,
        )

        assert changes is not None
        assert changes.ownership.is_empty()
        assert not build_ownership_units_from_batch_source_lines(
            ownership,
            source_lines,
        )
        with merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            file_lines,
        ) as merged:
            assert tuple(merged) == file_lines


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


def test_promote_untracked_presence_requires_exact_batch_predecessor() -> None:
    """Promotion succeeds before, but not after, an independent peel."""
    path = "file.txt"
    saved_file = (b"owned\n", b"keep\n", b"new\n")
    prior_worktree = (b"keep\n", b"new\n")
    rewritten = (b"keep\n", b"new\n", b"old\n")
    baseline_snapshot = content_snapshot(path, (), space=BaselineSpace)
    worktree_snapshot = content_snapshot(
        path,
        prior_worktree,
        space=WorktreeSpace,
    )
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

    with LineBuffer.from_chunks(saved_file) as source_lines:
        existing = SourceBoundOwnership(
            content_snapshot(path, source_lines, space=BatchSourceSpace),
            BatchOwnership.from_presence_lines(["1"]),
        )
        promoted = promote_untracked_presence_to_complete_source_replacement(
            source_lines,
            existing,
            rewritten_lines=rewritten,
            alternatives=alternatives,
        )
        assert promoted is not None
        with promoted:
            assert tuple(promoted.source_buffer) == (
                *saved_file,
                b"keep\n",
                b"old\n",
            )

        independently_peeled = (b"new\n", b"old\n")
        peeled_worktree = content_snapshot(
            path,
            (b"new\n",),
            space=WorktreeSpace,
        )
        peeled_rewritten = content_snapshot(
            path,
            independently_peeled,
            space=RewrittenWorktreeSpace,
        )
        peeled_edit = ReplacementEditPlan(
            path=path,
            baseline_snapshot=baseline_snapshot,
            worktree_snapshot=peeled_worktree,
            baseline_span=LineSpan(LineBoundary(0), LineBoundary(0)),
            worktree_span=LineSpan(LineBoundary(0), LineBoundary(1)),
        ).bind_result(peeled_rewritten, replacement_line_count=2)
        peeled_alternatives = ExplicitReplacementAlternatives(
            edit=peeled_edit,
            saved=SnapshotSpan(
                peeled_rewritten,
                LineSpan(LineBoundary(0), LineBoundary(1)),
            ),
            live=SnapshotSpan(
                peeled_rewritten,
                LineSpan(LineBoundary(1), LineBoundary(2)),
            ),
            parent=None,
            ownership_scope=ReplacementAlternativeOwnership.UNTRACKED_SOURCE,
        )
        assert (
            promote_untracked_presence_to_complete_source_replacement(
                source_lines,
                existing,
                rewritten_lines=independently_peeled,
                alternatives=peeled_alternatives,
            )
            is None
        )


def test_refresh_complete_source_replacement_advances_only_live_snapshot() -> None:
    """A re-peel keeps the complete saved file and replaces its predecessor."""
    path = "tool.c"
    saved_file = (
        b"caps = ASYNC_TX |\n",
        b"       RX_INJECT |\n",
        b"       TRANSPORT_STATE |\n",
        b"       EDID;\n",
        b"request path\n",
        b"tail\n",
    )
    old_live_file = (
        b"caps = ASYNC_TX |\n",
        b"       RX_INJECT |\n",
        b"       TRANSPORT_STATE |\n",
        b"       EDID;\n",
        b"tail\n",
    )
    prior_worktree = (
        b"caps = ASYNC_TX |\n",
        b"       TRANSPORT_STATE |\n",
        b"       EDID;\n",
        b"tail\n",
    )
    rewritten = (
        b"caps = ASYNC_TX |\n",
        b"       TRANSPORT_STATE |\n",
        b"caps = TRANSPORT_STATE |\n",
        b"       EDID;\n",
        b"tail\n",
    )
    baseline_snapshot = content_snapshot(path, (), space=BaselineSpace)
    worktree_snapshot = content_snapshot(
        path,
        prior_worktree,
        space=WorktreeSpace,
    )
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
        worktree_span=LineSpan(LineBoundary(0), LineBoundary(2)),
    ).bind_result(rewritten_snapshot, replacement_line_count=3)
    alternatives = ExplicitReplacementAlternatives(
        edit=edit,
        saved=SnapshotSpan(
            rewritten_snapshot,
            LineSpan(LineBoundary(0), LineBoundary(2)),
        ),
        live=SnapshotSpan(
            rewritten_snapshot,
            LineSpan(LineBoundary(2), LineBoundary(3)),
        ),
        parent=None,
        ownership_scope=ReplacementAlternativeOwnership.UNTRACKED_SOURCE,
    )

    with LineBuffer.from_chunks((*saved_file, *old_live_file)) as source_lines:
        refreshed = refresh_complete_source_replacement(
            source_lines,
            _complete_ownership(
                path,
                source_lines,
                saved_line_count=len(saved_file),
            ),
            rewritten_lines=rewritten,
            alternatives=alternatives,
        )
        assert refreshed is not None
        with refreshed:
            expected_live = (
                b"caps = TRANSPORT_STATE |\n",
                b"       EDID;\n",
                b"tail\n",
            )
            assert tuple(refreshed.source_buffer) == (*saved_file, *expected_live)
            assert (
                tuple(refreshed.bound_ownership.value.deletions[0].content_lines)
                == expected_live
            )
            assert (
                resolve_complete_source_replacement(
                    refreshed.source_buffer,
                    refreshed.bound_ownership,
                )
                is not None
            )


def test_refresh_complete_source_replacement_refuses_ambiguous_saved_line() -> None:
    """Repeated content without structural provenance cannot advance the target."""
    path = "file.txt"
    saved_file = (b"left\n", b"same\n", b"middle\n", b"same\n", b"right\n")
    old_live_file = (b"old\n",)
    prior_worktree = (b"before\n", b"same\n", b"after\n")
    rewritten = (b"before\n", b"same\n", b"live\n", b"after\n")
    baseline_snapshot = content_snapshot(path, (), space=BaselineSpace)
    worktree_snapshot = content_snapshot(
        path,
        prior_worktree,
        space=WorktreeSpace,
    )
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

    with LineBuffer.from_chunks((*saved_file, *old_live_file)) as source_lines:
        assert (
            refresh_complete_source_replacement(
                source_lines,
                _complete_ownership(
                    path,
                    source_lines,
                    saved_line_count=len(saved_file),
                ),
                rewritten_lines=rewritten,
                alternatives=alternatives,
            )
            is None
        )
