"""Repository-read-only construction of reusable rewrite-plan snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from ..exceptions import CommandError
from ..fixup.commutation import tree_for_commit
from ..fixup.staged_units import acquire_tree_fixup_units
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_environment import use_raw_git_object_graph
from .commit_objects import parse_commit_object
from .dependencies import analyze_history_dependencies
from .models import (
    CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
    HistoryCommitSnapshot,
    HistoryPatchUnit,
    HistoryPlan,
    HistoryPlanDocument,
    HistoryPlannedCommit,
    HistorySnapshot,
)
from .ranges import (
    ResolvedHistoryRange,
    resolve_exact_history_range,
    resolve_history_range,
)
from .safety import collect_history_safety_facts
from .snapshot_cache import (
    HistorySnapshotCacheObservation,
    acquire_cached_history_snapshot,
)
from .unit_ids import history_unit_id


def _symbolic_head() -> str | None:
    result = run_git_command(
        ["symbolic-ref", "--quiet", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _require_frozen_head(tip: str, branch_ref: str | None) -> None:
    current_tip = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        requires_index_lock=False,
    ).stdout.strip()
    if current_tip != tip or _symbolic_head() != branch_ref:
        raise CommandError(
            _("HEAD changed while the history snapshot was being built.")
        )


def _commit_snapshot(commit: str, expected_parent: str) -> HistoryCommitSnapshot:
    parsed = parse_commit_object(commit)
    if parsed.parents != (expected_parent,):
        raise CommandError(
            _("History changed while commit {commit} was scanned.").format(
                commit=commit
            )
        )
    parent_tree = tree_for_commit(expected_parent)
    with acquire_tree_fixup_units(expected_parent, commit) as fixup_units:
        units = tuple(
            HistoryPatchUnit(
                unit_id=history_unit_id(commit, unit.unit_id),
                patch_id=unit.unit_id,
                source_commit=commit,
                path=unit.path,
                kind=unit.kind,
                old_start=unit.old_start,
                old_len=unit.old_len,
                new_start=unit.new_start,
                new_len=unit.new_len,
                unsupported_reason=unit.unsupported_reason,
            )
            for unit in fixup_units
        )
    if len({unit.unit_id for unit in units}) != len(units):
        raise CommandError(
            _("Commit {commit} produced duplicate history unit IDs.").format(
                commit=commit
            )
        )
    return HistoryCommitSnapshot(
        commit_id=commit,
        parent=expected_parent,
        tree=parsed.tree,
        parent_tree=parent_tree,
        author=parsed.author,
        committer=parsed.committer,
        encoding=parsed.encoding,
        message=parsed.message,
        message_sha256=parsed.message_sha256,
        signatures=parsed.signatures,
        unsupported_headers=parsed.unsupported_headers,
        units=units,
    )


def _snapshot_from_range(
    commit_range: ResolvedHistoryRange,
    branch_ref: str | None,
    *,
    cache_observer: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> HistorySnapshot:
    base_tree = tree_for_commit(commit_range.base_commit)
    final_tree = tree_for_commit(commit_range.tip_commit)
    def build() -> HistorySnapshot:
        return _build_snapshot_from_range(
            commit_range,
            branch_ref,
            base_tree=base_tree,
            final_tree=final_tree,
        )
    if cache_observer is None:
        return acquire_cached_history_snapshot(
            commit_range,
            branch_ref,
            base_tree=base_tree,
            final_tree=final_tree,
            build=build,
        )
    return acquire_cached_history_snapshot(
        commit_range,
        branch_ref,
        base_tree=base_tree,
        final_tree=final_tree,
        build=build,
        observe=cache_observer,
    )


def _build_snapshot_from_range(
    commit_range: ResolvedHistoryRange,
    branch_ref: str | None,
    *,
    base_tree: str,
    final_tree: str,
) -> HistorySnapshot:
    commits: list[HistoryCommitSnapshot] = []
    expected_parent = commit_range.base_commit
    for commit in commit_range.commits_oldest_first:
        commit_snapshot = _commit_snapshot(commit, expected_parent)
        commits.append(commit_snapshot)
        expected_parent = commit

    frozen_commits = tuple(commits)
    if frozen_commits[-1].tree != final_tree:
        raise CommandError(
            _("History changed while commit {commit} was scanned.").format(
                commit=commit_range.tip_commit
            )
        )
    snapshot = HistorySnapshot(
        object_format=commit_range.object_format,
        base_commit=commit_range.base_commit,
        tip_commit=commit_range.tip_commit,
        movable_base=commit_range.movable_base,
        base_tree=base_tree,
        final_tree=final_tree,
        branch_ref=branch_ref,
        commits=frozen_commits,
        dependencies=(),
    )
    return replace(
        snapshot,
        dependencies=analyze_history_dependencies(snapshot),
    )


def acquire_frozen_history_snapshot(
    base_commit: str,
    tip_commit: str,
    branch_ref: str | None,
    *,
    movable_base: str | None = None,
    cache_observer: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> HistorySnapshot:
    """Acquire one exact source snapshot independently of current HEAD."""
    with use_raw_git_object_graph():
        return _snapshot_from_range(
            resolve_exact_history_range(
                base_commit, tip_commit, movable_base
            ),
            branch_ref,
            cache_observer=cache_observer,
        )


def acquire_history_plan_document(
    movable_boundary: str | None = None,
    *,
    onto_boundary: str | None = None,
    allowed_remote_refs: tuple[str, ...] = (),
    cache_observer: Callable[[HistorySnapshotCacheObservation], None] | None = None,
) -> HistoryPlanDocument:
    """Capture an immutable range and KEEP plan without operation-state writes.

    ``movable_boundary`` is the exclusive base of the commits that may move;
    ``onto_boundary`` is the older frozen base captured by the scan. When
    ``onto_boundary`` is omitted the frozen base equals the movable base and
    the whole range is movable.
    """
    with use_raw_git_object_graph():
        commit_range = resolve_history_range(onto_boundary, movable_boundary)
        branch_ref = _symbolic_head()
        history_snapshot = _snapshot_from_range(
            commit_range,
            branch_ref,
            cache_observer=cache_observer,
        )
        _require_frozen_head(commit_range.tip_commit, branch_ref)
        safety = collect_history_safety_facts(
            tip=history_snapshot.tip_commit,
            final_tree=history_snapshot.final_tree,
            branch_ref=history_snapshot.branch_ref,
            source_commits=tuple(
                commit.commit_id for commit in history_snapshot.commits
            ),
            allowed_remote_refs=allowed_remote_refs,
        )
        _require_frozen_head(commit_range.tip_commit, branch_ref)
        plan = HistoryPlan(
            partitioned_units=(),
            outputs=tuple(
                HistoryPlannedCommit(
                    operation="KEEP",
                    materialization="EXACT",
                    source_commits=(commit.commit_id,),
                    source_unit_ids=tuple(unit.unit_id for unit in commit.units),
                    message=commit.message,
                    encoding=commit.encoding,
                    author=commit.author,
                    rationale="",
                )
                for commit in history_snapshot.commits
            ),
        )
        return HistoryPlanDocument(
            schema_version=CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
            snapshot=history_snapshot,
            safety=safety,
            plan=plan,
        )
