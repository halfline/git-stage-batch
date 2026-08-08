"""JSON records for history snapshots and semantic plans."""

from __future__ import annotations

from .models import (
    HistoryCommitSnapshot,
    HistoryIdentity,
    HistoryPatchUnit,
    HistoryPlanDocument,
    HistoryPlannedCommit,
    HistorySafetyFacts,
    HistorySignature,
    HistorySnapshot,
)


def history_identity_record(identity: HistoryIdentity) -> dict[str, object]:
    """Return an exact machine-readable commit identity."""
    return {
        "raw": identity.raw,
        "name": identity.name,
        "email": identity.email,
        "timestamp": identity.timestamp,
        "timezone": identity.timezone,
    }


def _signature_record(signature: HistorySignature) -> dict[str, object]:
    return {
        "header": signature.header,
        "sha256": signature.sha256,
    }


def _unit_record(unit: HistoryPatchUnit) -> dict[str, object]:
    return {
        "id": unit.unit_id,
        "patch_id": unit.patch_id,
        "source_commit": unit.source_commit,
        "path": unit.path,
        "kind": unit.kind,
        "old_start": unit.old_start,
        "old_len": unit.old_len,
        "new_start": unit.new_start,
        "new_len": unit.new_len,
        "unsupported_reason": unit.unsupported_reason,
    }


def _commit_record(commit: HistoryCommitSnapshot) -> dict[str, object]:
    return {
        "id": commit.commit_id,
        "parent": commit.parent,
        "tree": commit.tree,
        "parent_tree": commit.parent_tree,
        "author": history_identity_record(commit.author),
        "committer": history_identity_record(commit.committer),
        "encoding": commit.encoding,
        "message": commit.message,
        "message_sha256": commit.message_sha256,
        "signatures": [
            _signature_record(signature) for signature in commit.signatures
        ],
        "patch": {
            "old_tree": commit.parent_tree,
            "new_tree": commit.tree,
            "units": [_unit_record(unit) for unit in commit.units],
        },
    }


def history_snapshot_record(snapshot: HistorySnapshot) -> dict[str, object]:
    """Return all immutable facts that plan validation must regenerate."""
    return {
        "object_format": snapshot.object_format,
        "range": {
            "base": snapshot.base_commit,
            "tip": snapshot.tip_commit,
            "commits_oldest_first": [
                commit.commit_id for commit in snapshot.commits
            ],
        },
        "trees": {
            "base": snapshot.base_tree,
            "final": snapshot.final_tree,
        },
        "branch_ref": snapshot.branch_ref,
        "rewritten_signatures_preserved": False,
        "commits": [_commit_record(commit) for commit in snapshot.commits],
    }


def history_safety_record(safety: HistorySafetyFacts) -> dict[str, object]:
    """Return live, informational mutation preconditions."""
    return {
        "index_tree": safety.index_tree,
        "index_clean": safety.index_clean,
        "worktree_clean": safety.worktree_clean,
        "untracked_path_count": safety.untracked_path_count,
        "staging_session_active": safety.staging_session_active,
        "saved_batches": list(safety.saved_batches),
        "active_git_operations": list(safety.active_git_operations),
        "active_rewrite_operation": safety.active_history_operation,
        "upstream_ref": safety.upstream_ref,
        "upstream_tip": safety.upstream_tip,
        "ahead_count": safety.ahead_count,
        "behind_count": safety.behind_count,
        "remote_refs_containing_tip": list(safety.remote_refs_containing_tip),
        "remote_containment": [
            {
                "commit": containment.commit_id,
                "remote_refs": list(containment.remote_refs),
            }
            for containment in safety.remote_containment
        ],
        "mutation_ready": safety.mutation_ready,
        "blockers": list(safety.blockers),
    }


def _planned_commit_record(output: HistoryPlannedCommit) -> dict[str, object]:
    return {
        "operation": output.operation,
        "source_commits": list(output.source_commits),
        "unit_ids": list(output.unit_ids),
        "message": output.message,
        "encoding": output.encoding,
        "author": history_identity_record(output.author),
        "rationale": output.rationale,
    }


def history_plan_document_record(
    document: HistoryPlanDocument,
) -> dict[str, object]:
    """Return one reusable, versioned rewrite-plan document."""
    return {
        "schema_version": document.schema_version,
        "operation": "rewrite-plan",
        "snapshot": history_snapshot_record(document.snapshot),
        "safety": history_safety_record(document.safety),
        "plan": {
            "outputs": [
                _planned_commit_record(output) for output in document.plan.outputs
            ]
        },
    }
