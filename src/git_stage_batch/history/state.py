"""Durable operation checkpoints stored under the common Git directory."""

from __future__ import annotations

import hashlib
import stat
import subprocess
from pathlib import Path
from typing import NoReturn, cast

from ..exceptions import CommandError
from ..git_paths import terminal_safe_text
from ..i18n import _
from ..utils.file_io import (
    AtomicWriteModePolicy,
    read_required_text_file_contents,
    read_required_text_file_contents_and_sha256,
    write_text_file_contents,
)
from ..utils.git_command import run_git_command
from ..utils.git_object_io import temporary_git_object_environment
from ..utils.paths import get_rewrite_state_directory_path
from ..utils.strict_json import (
    StrictJsonError,
    loads,
    require_exact_keys,
    require_integer,
    require_list,
    require_object,
    require_string,
)
from .json_files import history_json_sha256, write_history_json_file
from .models import (
    HISTORY_PLAN_OPERATIONS,
    HistoryNextAction,
    HistoryOperationInspection,
    HistoryOperationState,
    HistoryPhase,
    HistoryPlanDocument,
    HistoryPlanOperation,
)
from .records import history_plan_document_record
from .plan_files import read_and_validate_frozen_history_plan_semantics
from .resolution_files import (
    create_private_resolution_directory,
    list_resolution_directory,
    publish_private_resolution_directory,
    publish_new_private_file,
)
from .resolution_workspace import (
    copy_completed_history_resolution,
    materialize_completed_history_resolution,
)
from .safety import collect_history_safety_facts


_STATE_V2_KEYS = frozenset(
    {
        "schema_version",
        "operation_id",
        "phase",
        "next_action",
        "plan_sha256",
        "object_format",
        "branch_ref",
        "base_commit",
        "original_tip",
        "original_final_tree",
        "source_commits",
        "allowed_remote_refs",
        "recovery_ref",
        "output_ref",
        "expected_branch_tip",
        "planned_output_count",
        "output_commits",
        "completed_output_count",
        "pending_output_commit",
        "pending_output_tree",
        "last_verified_commit",
        "last_verified_tree",
        "verification_sha256",
        "diagnostic",
    }
)
_STATE_V3_KEYS = _STATE_V2_KEYS | {
    "resolution_raw_plan_sha256",
    "resolution_complete_sha256",
}
_SUPPORTED_STATE_SCHEMA_VERSIONS = frozenset({2, 3})
_IMMUTABLE_STATE_FIELDS = (
    "schema_version",
    "operation_id",
    "plan_sha256",
    "resolution_raw_plan_sha256",
    "resolution_complete_sha256",
    "object_format",
    "branch_ref",
    "base_commit",
    "original_tip",
    "original_final_tree",
    "source_commits",
    "allowed_remote_refs",
    "recovery_ref",
    "output_ref",
    "planned_output_count",
)
_ALLOWED_NEXT_ACTIONS = {
    HistoryPhase.PREPARED: frozenset({HistoryNextAction.BUILD_OUTPUT}),
    HistoryPhase.BUILDING: frozenset({HistoryNextAction.BUILD_OUTPUT}),
    HistoryPhase.PAUSED: frozenset(
        {
            HistoryNextAction.BUILD_OUTPUT,
            HistoryNextAction.VERIFY_SERIES,
            HistoryNextAction.UPDATE_BRANCH,
            HistoryNextAction.RESTORE_ORIGINAL,
        }
    ),
    HistoryPhase.VERIFYING: frozenset({HistoryNextAction.VERIFY_SERIES}),
    HistoryPhase.READY_TO_UPDATE: frozenset({HistoryNextAction.UPDATE_BRANCH}),
    HistoryPhase.COMPLETE: frozenset({HistoryNextAction.NONE}),
    HistoryPhase.ABORTED: frozenset({HistoryNextAction.NONE}),
}
_ALLOWED_TRANSITIONS = {
    HistoryPhase.PREPARED: frozenset(
        {
            HistoryPhase.BUILDING,
            HistoryPhase.PAUSED,
            HistoryPhase.ABORTED,
        }
    ),
    HistoryPhase.BUILDING: frozenset(
        {
            HistoryPhase.BUILDING,
            HistoryPhase.PAUSED,
            HistoryPhase.VERIFYING,
            HistoryPhase.ABORTED,
        }
    ),
    HistoryPhase.PAUSED: frozenset(
        {
            HistoryPhase.PAUSED,
            HistoryPhase.BUILDING,
            HistoryPhase.VERIFYING,
            HistoryPhase.READY_TO_UPDATE,
            HistoryPhase.ABORTED,
        }
    ),
    HistoryPhase.VERIFYING: frozenset(
        {
            HistoryPhase.VERIFYING,
            HistoryPhase.PAUSED,
            HistoryPhase.READY_TO_UPDATE,
            HistoryPhase.ABORTED,
        }
    ),
    HistoryPhase.READY_TO_UPDATE: frozenset(
        {
            HistoryPhase.PAUSED,
            HistoryPhase.COMPLETE,
            HistoryPhase.ABORTED,
        }
    ),
    HistoryPhase.COMPLETE: frozenset(),
    HistoryPhase.ABORTED: frozenset(),
}


def _invalid(detail: str) -> NoReturn:
    raise CommandError(
        _("Invalid rewrite operation state: {detail}").format(
            detail=terminal_safe_text(detail)
        )
    )


def _history_directory() -> Path:
    return get_rewrite_state_directory_path()


def _active_path() -> Path:
    return _history_directory() / "active"


def _latest_path() -> Path:
    return _history_directory() / "latest"


def _existing_history_directory() -> Path | None:
    path = _history_directory()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        _invalid(f"cannot inspect history state directory: {error}")
    if not stat.S_ISDIR(metadata.st_mode):
        _invalid("history state path must be a directory")
    return path


def _ensure_history_directory() -> Path:
    path = _existing_history_directory()
    if path is None:
        path = _history_directory()
        path.mkdir(parents=True, mode=0o700)
    return path


def _validate_operation_id(operation_id: str) -> None:
    if len(operation_id) != 32 or any(
        character not in "0123456789abcdef" for character in operation_id
    ):
        _invalid("operation_id must be 32 lowercase hexadecimal characters")


def history_operation_directory(operation_id: str) -> Path:
    """Return the private directory for one validated operation ID."""
    _validate_operation_id(operation_id)
    return _history_directory() / operation_id


def history_operation_preparation_directory(operation_id: str) -> Path:
    """Return the private unpublished staging directory for one operation."""
    _validate_operation_id(operation_id)
    return _history_directory() / f".prepare-{operation_id}"


def history_operation_plan_path(operation_id: str) -> Path:
    """Return the immutable persisted plan path for one operation."""
    return history_operation_directory(operation_id) / "plan.json"


def history_operation_resolutions_path(operation_id: str) -> Path:
    """Return the operation-owned completed resolution workspace path."""
    return history_operation_directory(operation_id) / "resolutions"


def history_operation_verification_path(operation_id: str) -> Path:
    """Return the independent verification record path for one operation."""
    return history_operation_directory(operation_id) / "verification.json"


def history_recovery_ref(operation_id: str) -> str:
    """Return the operation-owned ref that preserves the original tip."""
    _validate_operation_id(operation_id)
    return f"refs/git-stage-batch/rewrite/{operation_id}/original"


def history_output_ref(operation_id: str) -> str:
    """Return the operation-owned ref that preserves built output commits."""
    _validate_operation_id(operation_id)
    return f"refs/git-stage-batch/rewrite/{operation_id}/output"


def _require_regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        _invalid(f"cannot inspect {path.name}: {error}")
    if not stat.S_ISREG(metadata.st_mode):
        _invalid(f"{path.name} must be a regular file")


def _require_operation_directory(operation_id: str) -> Path:
    path = history_operation_directory(operation_id)
    try:
        metadata = path.lstat()
    except OSError as error:
        _invalid(f"cannot inspect operation directory: {error}")
    if not stat.S_ISDIR(metadata.st_mode):
        _invalid("operation state path must be a directory")
    return path


def _operation_pointer(path: Path, label: str) -> str | None:
    if _existing_history_directory() is None:
        return None
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        _invalid(f"cannot inspect {label} operation: {error}")
    if not stat.S_ISREG(metadata.st_mode):
        _invalid(f"{label} must be a regular file")
    try:
        operation_id = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        _invalid(f"cannot read {label} operation: {error}")
    _validate_operation_id(operation_id)
    return operation_id


def active_history_operation_id() -> str | None:
    """Return the exact active operation ID without creating state."""
    return _operation_pointer(_active_path(), "active")


def latest_history_operation_id() -> str | None:
    """Return the most recent terminal operation ID without creating state."""
    return _operation_pointer(_latest_path(), "latest")


def _nullable_string(
    record: dict[str, object],
    field: str,
    location: str,
) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid(f"{location}.{field} must be a string or null")
    return value


def _object_id_length(object_format: str) -> int:
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    _invalid("object_format must be 'sha1' or 'sha256'")


def _require_hex(value: str, length: int, location: str) -> None:
    if len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        _invalid(f"{location} must be lowercase hexadecimal with length {length}")


def _state_record(state: HistoryOperationState) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": state.schema_version,
        "operation_id": state.operation_id,
        "phase": state.phase.value,
        "next_action": state.next_action.value,
        "plan_sha256": state.plan_sha256,
        "object_format": state.object_format,
        "branch_ref": state.branch_ref,
        "base_commit": state.base_commit,
        "original_tip": state.original_tip,
        "original_final_tree": state.original_final_tree,
        "source_commits": list(state.source_commits),
        "allowed_remote_refs": list(state.allowed_remote_refs),
        "recovery_ref": state.recovery_ref,
        "output_ref": state.output_ref,
        "expected_branch_tip": state.expected_branch_tip,
        "planned_output_count": state.planned_output_count,
        "output_commits": list(state.output_commits),
        "completed_output_count": state.completed_output_count,
        "pending_output_commit": state.pending_output_commit,
        "pending_output_tree": state.pending_output_tree,
        "last_verified_commit": state.last_verified_commit,
        "last_verified_tree": state.last_verified_tree,
        "verification_sha256": state.verification_sha256,
        "diagnostic": state.diagnostic,
    }
    if state.schema_version == 2:
        if (
            state.resolution_raw_plan_sha256 is not None
            or state.resolution_complete_sha256 is not None
        ):
            _invalid("schema version 2 cannot contain resolution provenance")
        return record
    if state.schema_version == 3:
        record["resolution_raw_plan_sha256"] = (
            state.resolution_raw_plan_sha256
        )
        record["resolution_complete_sha256"] = state.resolution_complete_sha256
        return record
    _invalid("schema_version must be 2 or 3")


def _validate_state(state: HistoryOperationState) -> None:
    if state.schema_version not in _SUPPORTED_STATE_SCHEMA_VERSIONS:
        _invalid("schema_version must be 2 or 3")
    _validate_operation_id(state.operation_id)
    oid_length = _object_id_length(state.object_format)
    _require_hex(state.plan_sha256, 64, "plan_sha256")
    if (
        state.resolution_raw_plan_sha256 is None
    ) != (state.resolution_complete_sha256 is None):
        _invalid("resolution provenance digests must both be null or both be set")
    if state.resolution_raw_plan_sha256 is not None:
        _require_hex(
            state.resolution_raw_plan_sha256,
            64,
            "resolution_raw_plan_sha256",
        )
        _require_hex(
            cast(str, state.resolution_complete_sha256),
            64,
            "resolution_complete_sha256",
        )
    if state.schema_version == 2 and state.resolution_raw_plan_sha256 is not None:
        _invalid("schema version 2 cannot contain resolution provenance")
    for field, value in (
        ("base_commit", state.base_commit),
        ("original_tip", state.original_tip),
        ("original_final_tree", state.original_final_tree),
        ("expected_branch_tip", state.expected_branch_tip),
    ):
        _require_hex(value, oid_length, field)
    for index, commit in enumerate(state.output_commits):
        _require_hex(commit, oid_length, f"output_commits[{index}]")
    if not state.source_commits or state.source_commits[-1] != state.original_tip:
        _invalid("source_commits must be non-empty and end at original_tip")
    for index, commit in enumerate(state.source_commits):
        _require_hex(commit, oid_length, f"source_commits[{index}]")
    if len(set(state.source_commits)) != len(state.source_commits):
        _invalid("source_commits must not contain duplicates")
    if len(set(state.output_commits)) != len(state.output_commits):
        _invalid("output_commits must not contain duplicates")
    for index, refname in enumerate(state.allowed_remote_refs):
        if not refname.startswith("refs/remotes/") or "\0" in refname:
            _invalid(f"allowed_remote_refs[{index}] must be a full remote-tracking ref")
    if tuple(sorted(set(state.allowed_remote_refs))) != state.allowed_remote_refs:
        _invalid("allowed_remote_refs must be sorted and unique")
    if not state.branch_ref.startswith("refs/heads/") or "\0" in state.branch_ref:
        _invalid("branch_ref must be a full local branch ref")
    if state.recovery_ref != history_recovery_ref(state.operation_id):
        _invalid("recovery_ref does not belong to operation_id")
    if state.output_ref != history_output_ref(state.operation_id):
        _invalid("output_ref does not belong to operation_id")
    if state.planned_output_count < 1:
        _invalid("planned_output_count must be positive")
    if state.completed_output_count != len(state.output_commits):
        _invalid("completed_output_count must equal the output object count")
    if state.completed_output_count > state.planned_output_count:
        _invalid("completed_output_count exceeds planned_output_count")
    if (state.pending_output_commit is None) != (state.pending_output_tree is None):
        _invalid("pending output commit and tree must both be null or both be set")
    if state.pending_output_commit is not None:
        _require_hex(
            state.pending_output_commit,
            oid_length,
            "pending_output_commit",
        )
        _require_hex(
            cast(str, state.pending_output_tree),
            oid_length,
            "pending_output_tree",
        )
        if state.pending_output_commit in state.output_commits:
            _invalid("pending_output_commit must not already be completed")
        if state.completed_output_count >= state.planned_output_count:
            _invalid("a complete output sequence cannot contain a pending commit")
        if state.next_action is not HistoryNextAction.BUILD_OUTPUT:
            _invalid("a pending output requires BUILD_OUTPUT as the next action")
        if state.phase not in {HistoryPhase.BUILDING, HistoryPhase.PAUSED}:
            _invalid("a pending output requires BUILDING or PAUSED state")
    if (state.last_verified_commit is None) != (state.last_verified_tree is None):
        _invalid("last verified commit and tree must both be null or both be set")
    if state.last_verified_commit is not None:
        _require_hex(
            state.last_verified_commit,
            oid_length,
            "last_verified_commit",
        )
        _require_hex(
            cast(str, state.last_verified_tree),
            oid_length,
            "last_verified_tree",
        )
        if state.last_verified_commit not in state.output_commits:
            _invalid("last_verified_commit must be an output commit")
    if state.verification_sha256 is not None:
        _require_hex(state.verification_sha256, 64, "verification_sha256")
    if state.next_action not in _ALLOWED_NEXT_ACTIONS[state.phase]:
        _invalid("next_action is inconsistent with phase")
    if state.phase is HistoryPhase.PREPARED and (
        state.expected_branch_tip != state.original_tip
        or state.completed_output_count != 0
        or state.pending_output_commit is not None
        or state.last_verified_commit is not None
        or state.verification_sha256 is not None
    ):
        _invalid("PREPARED state must not contain built output")
    if state.phase in {
        HistoryPhase.VERIFYING,
        HistoryPhase.READY_TO_UPDATE,
        HistoryPhase.COMPLETE,
    } and (
        state.completed_output_count != state.planned_output_count
        or state.pending_output_commit is not None
    ):
        _invalid(f"{state.phase.value} requires every output commit")
    if state.phase in {HistoryPhase.READY_TO_UPDATE, HistoryPhase.COMPLETE}:
        if (
            not state.output_commits
            or state.last_verified_commit != state.output_commits[-1]
            or state.last_verified_tree != state.original_final_tree
            or state.verification_sha256 is None
        ):
            _invalid(f"{state.phase.value} requires complete verification")
    if state.phase is HistoryPhase.COMPLETE:
        if state.expected_branch_tip != state.output_commits[-1]:
            _invalid("COMPLETE must expect the rewritten branch tip")
    elif state.phase is HistoryPhase.ABORTED:
        if state.expected_branch_tip != state.original_tip:
            _invalid("ABORTED must expect the original branch tip")
        if state.pending_output_commit is not None:
            _invalid("ABORTED must not contain a pending output")
    elif state.expected_branch_tip != state.original_tip:
        _invalid("an unfinished operation must expect the original branch tip")


def _require_state_matches_plan(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
) -> dict[str, object]:
    snapshot = document.snapshot
    expected = {
        "object_format": snapshot.object_format,
        "branch_ref": snapshot.branch_ref,
        "base_commit": snapshot.base_commit,
        "original_tip": snapshot.tip_commit,
        "original_final_tree": snapshot.final_tree,
        "source_commits": tuple(commit.commit_id for commit in snapshot.commits),
        "planned_output_count": len(document.plan.outputs),
    }
    for field, value in expected.items():
        if getattr(state, field) != value:
            _invalid(f"{field} does not match the validated plan")
    plan_record = history_plan_document_record(document)
    if state.plan_sha256 != history_json_sha256(plan_record):
        _invalid("plan_sha256 does not match the validated plan")
    has_resolved_outputs = any(
        output.materialization == "RESOLVED" for output in document.plan.outputs
    )
    if has_resolved_outputs != (state.resolution_raw_plan_sha256 is not None):
        _invalid("resolution provenance does not match plan materialization")
    return plan_record


def _decode_state(payload: str) -> HistoryOperationState:
    try:
        raw = loads(payload)
        record = require_object(raw, "state")
        schema_version = require_integer(record, "schema_version", "state")
        if schema_version == 2:
            require_exact_keys(record, _STATE_V2_KEYS, "state")
        elif schema_version == 3:
            require_exact_keys(record, _STATE_V3_KEYS, "state")
        else:
            _invalid("schema_version must be 2 or 3")
        phase_value = require_string(record, "phase", "state")
        action_value = require_string(record, "next_action", "state")
        try:
            phase = HistoryPhase(phase_value)
            action = HistoryNextAction(action_value)
        except ValueError as error:
            _invalid(str(error))
        output_values = require_list(record["output_commits"], "output_commits")
        output_commits: list[str] = []
        for index, value in enumerate(output_values):
            if not isinstance(value, str):
                _invalid(f"output_commits[{index}] must be a string")
            output_commits.append(value)
        source_values = require_list(record["source_commits"], "source_commits")
        source_commits: list[str] = []
        for index, value in enumerate(source_values):
            if not isinstance(value, str):
                _invalid(f"source_commits[{index}] must be a string")
            source_commits.append(value)
        allowed_values = require_list(
            record["allowed_remote_refs"],
            "allowed_remote_refs",
        )
        allowed_remote_refs: list[str] = []
        for index, value in enumerate(allowed_values):
            if not isinstance(value, str):
                _invalid(f"allowed_remote_refs[{index}] must be a string")
            allowed_remote_refs.append(value)
        state = HistoryOperationState(
            schema_version=schema_version,
            operation_id=require_string(record, "operation_id", "state"),
            phase=phase,
            next_action=action,
            plan_sha256=require_string(record, "plan_sha256", "state"),
            object_format=require_string(record, "object_format", "state"),
            branch_ref=require_string(record, "branch_ref", "state"),
            base_commit=require_string(record, "base_commit", "state"),
            original_tip=require_string(record, "original_tip", "state"),
            original_final_tree=require_string(
                record,
                "original_final_tree",
                "state",
            ),
            source_commits=tuple(source_commits),
            allowed_remote_refs=tuple(allowed_remote_refs),
            recovery_ref=require_string(record, "recovery_ref", "state"),
            output_ref=require_string(record, "output_ref", "state"),
            expected_branch_tip=require_string(
                record,
                "expected_branch_tip",
                "state",
            ),
            planned_output_count=require_integer(
                record,
                "planned_output_count",
                "state",
            ),
            output_commits=tuple(output_commits),
            completed_output_count=require_integer(
                record,
                "completed_output_count",
                "state",
            ),
            pending_output_commit=_nullable_string(
                record,
                "pending_output_commit",
                "state",
            ),
            pending_output_tree=_nullable_string(
                record,
                "pending_output_tree",
                "state",
            ),
            last_verified_commit=_nullable_string(
                record,
                "last_verified_commit",
                "state",
            ),
            last_verified_tree=_nullable_string(
                record,
                "last_verified_tree",
                "state",
            ),
            verification_sha256=_nullable_string(
                record,
                "verification_sha256",
                "state",
            ),
            diagnostic=_nullable_string(record, "diagnostic", "state"),
            resolution_raw_plan_sha256=(
                None
                if schema_version == 2
                else _nullable_string(
                    record,
                    "resolution_raw_plan_sha256",
                    "state",
                )
            ),
            resolution_complete_sha256=(
                None
                if schema_version == 2
                else _nullable_string(
                    record,
                    "resolution_complete_sha256",
                    "state",
                )
            ),
        )
    except StrictJsonError as error:
        _invalid(str(error))
    _validate_state(state)
    return state


def _load_history_operation(operation_id: str) -> HistoryOperationState:
    state_path = _require_operation_directory(operation_id) / "state.json"
    _require_regular_file(state_path)
    try:
        payload = read_required_text_file_contents(state_path)
    except (OSError, ValueError) as error:
        _invalid(f"cannot read state.json: {error}")
    state = _decode_state(payload)
    if state.operation_id != operation_id:
        _invalid("operation pointer and state operation_id disagree")
    return state


def load_active_history_operation() -> HistoryOperationState | None:
    """Load and strictly validate the active durable checkpoint."""
    operation_id = active_history_operation_id()
    return None if operation_id is None else _load_history_operation(operation_id)


def load_latest_history_operation() -> HistoryOperationState | None:
    """Load the latest terminal checkpoint when one exists."""
    operation_id = latest_history_operation_id()
    if operation_id is None:
        return None
    state = _load_history_operation(operation_id)
    if state.phase not in {
        HistoryPhase.COMPLETE,
        HistoryPhase.ABORTED,
    }:
        _invalid("latest must point to a terminal operation")
    return state


def load_history_operation_for_status() -> tuple[HistoryOperationState | None, bool]:
    """Return the active operation, or the latest terminal operation."""
    active = load_active_history_operation()
    if active is not None:
        return active, True
    return load_latest_history_operation(), False


def prepare_history_operation(
    state: HistoryOperationState,
    document: HistoryPlanDocument,
    *,
    resolutions_source: str | None,
) -> Path:
    """Build one complete private operation directory without publishing it."""
    _validate_state(state)
    if state.phase is not HistoryPhase.PREPARED:
        _invalid("a new operation must start in PREPARED")
    plan_record = _require_state_matches_plan(state, document)
    if active_history_operation_id() is not None:
        raise CommandError(_("Another rewrite operation is already active."))
    if _ref_is_symbolic(state.recovery_ref) or _resolved_commit(
        state.recovery_ref
    ) not in {None, state.original_tip}:
        _invalid("recovery_ref must be absent or preserve original_tip")
    if _resolved_object(state.output_ref) is not None:
        _invalid("output_ref must not exist before output is built")

    has_resolution = state.resolution_raw_plan_sha256 is not None
    if has_resolution != (resolutions_source is not None):
        _invalid("resolution source does not match operation provenance")

    _ensure_history_directory()
    preparation = history_operation_preparation_directory(state.operation_id)
    operation_directory = history_operation_directory(state.operation_id)
    for path, label in (
        (preparation, "operation preparation directory"),
        (operation_directory, "operation directory"),
    ):
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            _invalid(f"cannot inspect {label}: {error}")
        else:
            _invalid(f"{label} already exists")

    create_private_resolution_directory(preparation)
    if resolutions_source is not None:
        raw_plan_sha256 = cast(str, state.resolution_raw_plan_sha256)
        complete_sha256 = cast(str, state.resolution_complete_sha256)
        with temporary_git_object_environment(
            disable_replace_objects=True
        ) as source_quarantine:
            with temporary_git_object_environment(
                disable_replace_objects=True
            ) as destination_quarantine:
                authenticated = copy_completed_history_resolution(
                    document,
                    raw_plan_sha256,
                    resolutions_source,
                    str(preparation / "resolutions"),
                    source_quarantine=source_quarantine,
                    destination_quarantine=destination_quarantine,
                )
        if authenticated.complete_sha256 != complete_sha256:
            _invalid("resolution completion digest changed during preparation")

    write_history_json_file(
        preparation / "plan.json",
        plan_record,
        mode_policy=AtomicWriteModePolicy.PRIVATE,
    )
    write_history_json_file(
        preparation / "state.json",
        _state_record(state),
        mode_policy=AtomicWriteModePolicy.PRIVATE,
    )
    expected_entries = {"plan.json", "state.json"}
    if has_resolution:
        expected_entries.add("resolutions")
    actual_entries = set(list_resolution_directory(preparation))
    if actual_entries != expected_entries:
        _invalid("operation preparation contains unexpected entries")
    return preparation


def publish_prepared_history_operation(
    state: HistoryOperationState,
    preparation: Path,
) -> None:
    """Publish one complete operation directory after its recovery ref exists."""
    expected_preparation = history_operation_preparation_directory(
        state.operation_id
    )
    if preparation != expected_preparation:
        _invalid("operation preparation path does not belong to operation_id")
    if active_history_operation_id() is not None:
        raise CommandError(_("Another rewrite operation is already active."))
    if (
        _ref_is_symbolic(state.recovery_ref)
        or _resolved_commit(state.recovery_ref) != state.original_tip
    ):
        _invalid("recovery_ref must already preserve original_tip")
    if _resolved_object(state.output_ref) is not None:
        _invalid("output_ref must not exist before output is built")
    expected_entries = {"plan.json", "state.json"}
    if state.resolution_raw_plan_sha256 is not None:
        expected_entries.add("resolutions")
    if set(list_resolution_directory(preparation)) != expected_entries:
        _invalid("operation preparation contains unexpected entries")

    publish_private_resolution_directory(
        preparation,
        history_operation_directory(state.operation_id),
    )
    persisted = _load_history_operation(state.operation_id)
    if persisted != state:
        _invalid("published operation state does not match its preparation")
    inspection = inspect_history_operation(state, require_active=False)
    if not inspection.resume_ready:
        _invalid(
            "published operation is not activation-ready: "
            + ", ".join(inspection.blockers)
        )


def activate_prepared_history_operation(state: HistoryOperationState) -> None:
    """Publish the create-only active pointer after every durable prerequisite."""
    persisted = _load_history_operation(state.operation_id)
    if persisted != state:
        _invalid("published operation state changed before activation")
    active_operation_id = active_history_operation_id()
    if active_operation_id == state.operation_id:
        inspection = inspect_history_operation(state, require_active=True)
        if not inspection.resume_ready:
            _invalid(
                "active operation is not resume-ready: "
                + ", ".join(inspection.blockers)
            )
        return
    if active_operation_id is not None:
        raise CommandError(_("Another rewrite operation is already active."))
    if (
        _ref_is_symbolic(state.recovery_ref)
        or _resolved_commit(state.recovery_ref) != state.original_tip
    ):
        _invalid("recovery_ref must preserve original_tip before activation")
    inspection = inspect_history_operation(state, require_active=False)
    if not inspection.resume_ready:
        _invalid(
            "published operation is not activation-ready: "
            + ", ".join(inspection.blockers)
        )
    publish_new_private_file(
        _active_path(),
        f"{state.operation_id}\n".encode("ascii"),
        maximum_bytes=33,
    )


def update_history_operation(state: HistoryOperationState) -> None:
    """Atomically persist one allowed operation transition."""
    _validate_state(state)
    current = load_active_history_operation()
    if current is None or current.operation_id != state.operation_id:
        _invalid("operation is not active")
    for field in _IMMUTABLE_STATE_FIELDS:
        if getattr(state, field) != getattr(current, field):
            _invalid(f"{field} is immutable across operation transitions")
    if (
        state.output_commits[: len(current.output_commits)]
        != current.output_commits
        or len(state.output_commits) > len(current.output_commits) + 1
    ):
        _invalid("output_commits must preserve and extend the completed prefix")
    if state.phase not in _ALLOWED_TRANSITIONS[current.phase]:
        _invalid(
            f"phase transition {current.phase.value} -> {state.phase.value} "
            "is not allowed"
        )
    completed_output_tip = state.output_commits[-1] if state.output_commits else None
    allowed_output_tips = {completed_output_tip}
    if state.pending_output_commit is not None:
        allowed_output_tips.add(state.pending_output_commit)
    if (
        _ref_is_symbolic(state.output_ref)
        or _resolved_object(state.output_ref) not in allowed_output_tips
    ):
        _invalid("output_ref does not preserve the recorded output chain")
    if (
        _ref_is_symbolic(state.recovery_ref)
        or _resolved_commit(state.recovery_ref) != state.original_tip
    ):
        _invalid("recovery_ref no longer preserves original_tip")
    if (
        state.verification_sha256 is not None
        and _file_sha256(history_operation_verification_path(state.operation_id))
        != state.verification_sha256
    ):
        _invalid("verification record does not match verification_sha256")
    write_history_json_file(
        history_operation_directory(state.operation_id) / "state.json",
        _state_record(state),
        mode_policy=AtomicWriteModePolicy.PRIVATE,
    )


def write_history_verification_record(
    operation_id: str,
    record: dict[str, object],
) -> str:
    """Persist one private verification record and return its exact digest."""
    _require_operation_directory(operation_id)
    digest = history_json_sha256(record)
    write_history_json_file(
        history_operation_verification_path(operation_id),
        record,
        mode_policy=AtomicWriteModePolicy.PRIVATE,
    )
    return digest


def deactivate_history_operation(state: HistoryOperationState) -> None:
    """Move one terminal checkpoint from the active pointer to latest."""
    if state.phase not in {
        HistoryPhase.COMPLETE,
        HistoryPhase.ABORTED,
    }:
        _invalid("only a terminal operation can be deactivated")
    persisted = _load_history_operation(state.operation_id)
    if persisted != state:
        _invalid("terminal operation state changed before deactivation")
    active_id = active_history_operation_id()
    if active_id is None:
        if latest_history_operation_id() == state.operation_id:
            return
        _invalid("terminal operation is neither active nor latest")
    if active_id != state.operation_id:
        _invalid("another rewrite operation is active")

    latest_path = _latest_path()
    try:
        latest_metadata = latest_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        _invalid(f"cannot inspect latest operation: {error}")
    else:
        if not stat.S_ISREG(latest_metadata.st_mode):
            _invalid("latest must be a regular file")
    write_text_file_contents(latest_path, f"{state.operation_id}\n")

    active_path = _active_path()
    _require_regular_file(active_path)
    try:
        if active_path.read_text(encoding="ascii").strip() != state.operation_id:
            _invalid("active operation changed before deactivation")
        active_path.unlink()
    except (OSError, UnicodeError) as error:
        _invalid(f"cannot deactivate terminal operation: {error}")


def _file_sha256(path: Path) -> str | None:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        digest = hashlib.sha256()
        with path.open("rb") as file_handle:
            while chunk := file_handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _symbolic_head() -> str | None:
    result = run_git_command(
        ["symbolic-ref", "--quiet", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _resolved_commit(revision: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _resolved_object(revision: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", revision],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _ref_is_symbolic(refname: str) -> bool:
    result = run_git_command(
        ["symbolic-ref", "--quiet", refname],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode in {0, 1}:
        return result.returncode == 0
    raise CommandError(
        _("Could not inspect history ref {refname}.").format(refname=refname)
    )


def _resolved_tree(commit: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", f"{commit}^{{tree}}"],
        check=False,
        requires_index_lock=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _persisted_plan_facts(
    state: HistoryOperationState,
) -> tuple[bool, tuple[tuple[HistoryPlanOperation, int], ...]]:
    plan_path = history_operation_plan_path(state.operation_id)
    try:
        payload, plan_sha256 = read_required_text_file_contents_and_sha256(plan_path)
        if plan_sha256 != state.plan_sha256:
            return False, ()
        document = require_object(
            loads(payload),
            "document",
        )
        snapshot = require_object(document["snapshot"], "snapshot")
        range_record = require_object(snapshot["range"], "snapshot.range")
        trees = require_object(snapshot["trees"], "snapshot.trees")
        plan = require_object(document["plan"], "plan")
        source_values = require_list(
            range_record["commits_oldest_first"],
            "snapshot.range.commits_oldest_first",
        )
        source_commits = tuple(
            value for value in source_values if isinstance(value, str)
        )
        outputs = require_list(plan["outputs"], "plan.outputs")
        operation_counts = dict.fromkeys(HISTORY_PLAN_OPERATIONS, 0)
        for index, output_value in enumerate(outputs):
            output = require_object(output_value, f"plan.outputs[{index}]")
            operation_value = require_string(
                output,
                "operation",
                f"plan.outputs[{index}]",
            )
            if operation_value not in HISTORY_PLAN_OPERATIONS:
                return False, ()
            operation = operation_value
            operation_counts[operation] += 1
        matches = (
            len(source_commits) == len(source_values)
            and snapshot.get("object_format") == state.object_format
            and snapshot.get("branch_ref") == state.branch_ref
            and range_record.get("base") == state.base_commit
            and range_record.get("tip") == state.original_tip
            and trees.get("final") == state.original_final_tree
            and source_commits == state.source_commits
            and len(outputs) == state.planned_output_count
        )
        if not matches:
            return False, ()
        return True, tuple(
            (operation, operation_counts[operation])
            for operation in HISTORY_PLAN_OPERATIONS
            if operation_counts[operation]
        )
    except (KeyError, OSError, StrictJsonError, UnicodeError, ValueError):
        return False, ()


def _operation_resolution_matches(state: HistoryOperationState) -> bool | None:
    raw_plan_sha256 = state.resolution_raw_plan_sha256
    if raw_plan_sha256 is None:
        return None
    try:
        document, plan_sha256 = read_and_validate_frozen_history_plan_semantics(
            str(history_operation_plan_path(state.operation_id)),
            base_commit=state.base_commit,
            tip_commit=state.original_tip,
            branch_ref=state.branch_ref,
            allowed_remote_refs=state.allowed_remote_refs,
        )
        if plan_sha256 != state.plan_sha256:
            return False
        with temporary_git_object_environment(
            disable_replace_objects=True
        ) as quarantine:
            authenticated = materialize_completed_history_resolution(
                document,
                raw_plan_sha256,
                str(history_operation_resolutions_path(state.operation_id)),
                quarantine=quarantine,
            )
        return authenticated.complete_sha256 == state.resolution_complete_sha256
    except (
        CommandError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ):
        return False


def inspect_history_operation(
    state: HistoryOperationState,
    *,
    require_active: bool = True,
) -> HistoryOperationInspection:
    """Revalidate owned refs, objects, plan, index, and worktree."""
    _validate_state(state)
    current_branch = _symbolic_head()
    current_tip = _resolved_commit("HEAD")
    safety = collect_history_safety_facts(
        tip=current_tip or state.expected_branch_tip,
        final_tree=state.original_final_tree,
        branch_ref=current_branch,
        source_commits=state.source_commits,
        allowed_remote_refs=state.allowed_remote_refs,
    )
    recovery_matches = (
        not _ref_is_symbolic(state.recovery_ref)
        and _resolved_commit(state.recovery_ref) == state.original_tip
    )
    plan_matches, plan_operation_counts = _persisted_plan_facts(state)
    resolution_matches = _operation_resolution_matches(state)
    output_objects_exist = all(
        _resolved_commit(commit) == commit for commit in state.output_commits
    )
    completed_output_tip = state.output_commits[-1] if state.output_commits else None
    live_output_tip = _resolved_object(state.output_ref)
    allowed_output_tips = {completed_output_tip}
    if state.pending_output_commit is not None:
        allowed_output_tips.add(state.pending_output_commit)
    output_ref_matches = (
        not _ref_is_symbolic(state.output_ref)
        and live_output_tip in allowed_output_tips
    )
    if (
        output_objects_exist
        and state.last_verified_commit is not None
        and _resolved_tree(state.last_verified_commit) != state.last_verified_tree
    ):
        output_objects_exist = False

    branch_ref_matches = current_branch == state.branch_ref
    allowed_branch_tips = {state.expected_branch_tip}
    if (
        state.phase is HistoryPhase.READY_TO_UPDATE
        or (
            state.phase is HistoryPhase.PAUSED
            and state.next_action
            in {
                HistoryNextAction.UPDATE_BRANCH,
                HistoryNextAction.RESTORE_ORIGINAL,
            }
        )
    ) and state.output_commits:
        allowed_branch_tips.add(state.output_commits[-1])
    branch_tip_matches = current_tip in allowed_branch_tips
    index_matches = safety.index_tree == state.original_final_tree
    verification_matches = (
        state.verification_sha256 is None
        or _file_sha256(history_operation_verification_path(state.operation_id))
        == state.verification_sha256
    )
    blockers: list[str] = []
    for condition, code in (
        (branch_ref_matches, "branch-ref-changed"),
        (branch_tip_matches, "branch-tip-changed"),
        (index_matches, "staged-index"),
        (safety.worktree_clean, "tracked-worktree"),
        (recovery_matches, "recovery-ref-changed"),
        (plan_matches, "plan-changed"),
        (resolution_matches is not False, "resolution-bundle-changed"),
        (output_objects_exist, "output-object-missing"),
        (output_ref_matches, "output-ref-changed"),
        (verification_matches, "verification-record-changed"),
    ):
        if not condition:
            blockers.append(code)
    if require_active:
        blockers.extend(
            blocker
            for blocker in safety.blockers
            if blocker
            not in {
                "detached-head",
                "staged-index",
                "tracked-worktree",
                "active-rewrite-operation",
            }
        )
    if require_active and safety.active_history_operation != state.operation_id:
        blockers.append("active-rewrite-operation")
    if not require_active and safety.active_history_operation is not None:
        blockers.append("active-rewrite-operation")
    return HistoryOperationInspection(
        branch_ref_matches=branch_ref_matches,
        branch_tip_matches=branch_tip_matches,
        index_matches=index_matches,
        worktree_clean=safety.worktree_clean,
        recovery_ref_matches=recovery_matches,
        plan_matches=plan_matches,
        resolution_matches=resolution_matches,
        output_objects_exist=output_objects_exist,
        output_ref_matches=output_ref_matches,
        verification_matches=verification_matches,
        plan_operation_counts=plan_operation_counts,
        blockers=tuple(dict.fromkeys(blockers)),
    )
