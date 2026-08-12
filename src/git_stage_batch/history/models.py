"""Immutable models for history snapshots, plans, and safety facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ..fixup.models import FixupUnitKind


CURRENT_HISTORY_PLAN_SCHEMA_VERSION = 4
CURRENT_HISTORY_STATE_SCHEMA_VERSION = 3

HistoryPlanOperation = Literal[
    "KEEP",
    "REWORD",
    "INTEGRATE",
    "SPLIT",
    "REORDER",
]
HISTORY_PLAN_OPERATIONS: tuple[HistoryPlanOperation, ...] = (
    "KEEP",
    "REWORD",
    "INTEGRATE",
    "SPLIT",
    "REORDER",
)
HistoryDependencyBarrier = Literal["BLOCKED", "UNKNOWN"]
HistoryPlanMaterialization = Literal["EXACT", "RESOLVED"]
HISTORY_PLAN_MATERIALIZATIONS: tuple[HistoryPlanMaterialization, ...] = (
    "EXACT",
    "RESOLVED",
)


class HistoryPhase(str, Enum):
    """Closed lifecycle for one durable rewrite operation."""

    PREPARED = "PREPARED"
    BUILDING = "BUILDING"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    READY_TO_UPDATE = "READY_TO_UPDATE"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class HistoryNextAction(str, Enum):
    """Exact idempotent transition a continuation should perform."""

    BUILD_OUTPUT = "BUILD_OUTPUT"
    VERIFY_SERIES = "VERIFY_SERIES"
    UPDATE_BRANCH = "UPDATE_BRANCH"
    RESTORE_ORIGINAL = "RESTORE_ORIGINAL"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class HistoryIdentity:
    """Exact commit identity header with parsed date components."""

    raw: str
    name: str
    email: str
    timestamp: int
    timezone: str


@dataclass(frozen=True, slots=True)
class HistorySignature:
    """Content identity for a signature header removed by rewriting."""

    header: str
    sha256: str


@dataclass(frozen=True, slots=True)
class HistoryPatchUnit:
    """Stable metadata for one bounded exact patch unit."""

    unit_id: str
    patch_id: str
    source_commit: str
    path: str
    kind: FixupUnitKind
    old_start: int | None
    old_len: int | None
    new_start: int | None
    new_len: int | None
    unsupported_reason: str | None


@dataclass(frozen=True, slots=True)
class HistoryCommitSnapshot:
    """Immutable commit metadata and exact parent-to-tree unit inventory."""

    commit_id: str
    parent: str
    tree: str
    parent_tree: str
    author: HistoryIdentity
    committer: HistoryIdentity
    encoding: str | None
    message: str
    message_sha256: str
    signatures: tuple[HistorySignature, ...]
    unsupported_headers: tuple[str, ...]
    units: tuple[HistoryPatchUnit, ...]


@dataclass(frozen=True, slots=True)
class HistoryUnitDependency:
    """Compressed backward-commutation evidence for one exact source unit."""

    unit_id: str
    original_position: int
    earliest_position: int
    barrier_unit_id: str | None
    barrier: HistoryDependencyBarrier | None
    detail: str | None


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """Frozen linear source range consumed by one semantic plan."""

    object_format: str
    base_commit: str
    tip_commit: str
    base_tree: str
    final_tree: str
    branch_ref: str | None
    commits: tuple[HistoryCommitSnapshot, ...]
    dependencies: tuple[HistoryUnitDependency, ...]


@dataclass(frozen=True, slots=True)
class HistoryRemoteContainment:
    """Remote-tracking refs that contain one exact source commit."""

    commit_id: str
    remote_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistorySafetyFacts:
    """Live facts that determine whether a new rewrite may start."""

    index_tree: str | None
    index_clean: bool
    worktree_clean: bool
    untracked_path_count: int
    staging_session_active: bool
    saved_batches: tuple[str, ...]
    active_git_operations: tuple[str, ...]
    active_history_operation: str | None
    upstream_ref: str | None
    upstream_tip: str | None
    ahead_count: int | None
    behind_count: int | None
    remote_refs_containing_tip: tuple[str, ...]
    remote_containment: tuple[HistoryRemoteContainment, ...]
    blockers: tuple[str, ...]

    @property
    def mutation_ready(self) -> bool:
        """Return whether all local mechanical mutation preconditions hold."""
        return not self.blockers


@dataclass(frozen=True, slots=True)
class HistoryPlannedCommit:
    """One semantically reviewed output commit declaration."""

    operation: HistoryPlanOperation
    materialization: HistoryPlanMaterialization
    source_commits: tuple[str, ...]
    source_unit_ids: tuple[str, ...]
    message: str
    encoding: str | None
    author: HistoryIdentity
    rationale: str


@dataclass(frozen=True, slots=True)
class HistoryPartitionedUnit:
    """One mechanical source unit represented in several resolved outputs."""

    unit_id: str
    output_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class HistoryPlan:
    """Ordered intended replacement commits."""

    partitioned_units: tuple[HistoryPartitionedUnit, ...]
    outputs: tuple[HistoryPlannedCommit, ...]


@dataclass(frozen=True, slots=True)
class HistoryPlanDocument:
    """Reusable scan snapshot plus editable semantic plan."""

    schema_version: int
    snapshot: HistorySnapshot
    safety: HistorySafetyFacts
    plan: HistoryPlan


@dataclass(frozen=True, slots=True)
class HistoryOperationState:
    """Durable checkpoint for one local branch rewrite."""

    schema_version: int
    operation_id: str
    phase: HistoryPhase
    next_action: HistoryNextAction
    plan_sha256: str
    resolution_raw_plan_sha256: str | None
    resolution_complete_sha256: str | None
    object_format: str
    branch_ref: str
    base_commit: str
    original_tip: str
    original_final_tree: str
    source_commits: tuple[str, ...]
    allowed_remote_refs: tuple[str, ...]
    recovery_ref: str
    output_ref: str
    expected_branch_tip: str
    planned_output_count: int
    output_commits: tuple[str, ...]
    completed_output_count: int
    pending_output_commit: str | None
    pending_output_tree: str | None
    last_verified_commit: str | None
    last_verified_tree: str | None
    verification_sha256: str | None
    diagnostic: str | None


@dataclass(frozen=True, slots=True)
class HistoryOperationInspection:
    """Live resumability facts for a durable rewrite checkpoint."""

    branch_ref_matches: bool
    branch_tip_matches: bool
    index_matches: bool
    worktree_clean: bool
    recovery_ref_matches: bool
    plan_matches: bool
    resolution_matches: bool | None
    output_objects_exist: bool
    output_ref_matches: bool
    verification_matches: bool
    plan_operation_counts: tuple[tuple[HistoryPlanOperation, int], ...]
    blockers: tuple[str, ...]

    @property
    def resume_ready(self) -> bool:
        """Return whether continuation can trust all checkpoint ownership."""
        return not self.blockers


@dataclass(frozen=True, slots=True)
class HistoryVerification:
    """Independent proof for one completely built replacement series."""

    operation_id: str
    original_tip: str
    output_tip: str
    final_tree: str
    output_commits: tuple[str, ...]
    removed_signatures: tuple[tuple[str, HistorySignature], ...]
