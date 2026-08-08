"""Read-only construction of reusable rewrite-plan snapshots."""

from __future__ import annotations

from ..exceptions import CommandError
from ..fixup.commutation import tree_for_commit
from ..fixup.staged_units import acquire_tree_fixup_units
from ..i18n import _
from ..utils.git_command import run_git_command
from .commit_objects import parse_commit_object
from .models import (
    CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
    HistoryCommitSnapshot,
    HistoryPatchUnit,
    HistoryPlan,
    HistoryPlanDocument,
    HistoryPlannedCommit,
    HistorySnapshot,
)
from .ranges import resolve_history_range
from .safety import collect_history_safety_facts
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
        units=units,
    )


def acquire_history_plan_document(boundary: str | None) -> HistoryPlanDocument:
    """Capture an immutable range and a KEEP plan template without state writes."""
    commit_range = resolve_history_range(boundary)
    branch_ref = _symbolic_head()
    commits: list[HistoryCommitSnapshot] = []
    expected_parent = commit_range.base_commit
    for commit in commit_range.commits_oldest_first:
        commit_snapshot = _commit_snapshot(commit, expected_parent)
        commits.append(commit_snapshot)
        expected_parent = commit

    _require_frozen_head(commit_range.tip_commit, branch_ref)

    frozen_commits = tuple(commits)
    history_snapshot = HistorySnapshot(
        object_format=commit_range.object_format,
        base_commit=commit_range.base_commit,
        tip_commit=commit_range.tip_commit,
        base_tree=tree_for_commit(commit_range.base_commit),
        final_tree=frozen_commits[-1].tree,
        branch_ref=branch_ref,
        commits=frozen_commits,
    )
    safety = collect_history_safety_facts(
        tip=history_snapshot.tip_commit,
        final_tree=history_snapshot.final_tree,
        branch_ref=history_snapshot.branch_ref,
        source_commits=tuple(
            commit.commit_id for commit in history_snapshot.commits
        ),
    )
    _require_frozen_head(commit_range.tip_commit, branch_ref)
    plan = HistoryPlan(
        outputs=tuple(
            HistoryPlannedCommit(
                operation="KEEP",
                source_commits=(commit.commit_id,),
                unit_ids=tuple(unit.unit_id for unit in commit.units),
                message=commit.message,
                encoding=commit.encoding,
                author=commit.author,
                rationale="",
            )
            for commit in history_snapshot.commits
        )
    )
    return HistoryPlanDocument(
        schema_version=CURRENT_HISTORY_PLAN_SCHEMA_VERSION,
        snapshot=history_snapshot,
        safety=safety,
        plan=plan,
    )
