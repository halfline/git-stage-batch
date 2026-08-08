"""Tests for durable history-operation checkpoints."""

from __future__ import annotations

import json
import stat
from dataclasses import replace

import pytest

from git_stage_batch.commands.rewrite_status import command_rewrite_status
from git_stage_batch.exceptions import CommandError
from git_stage_batch.history.json_files import history_json_sha256
from git_stage_batch.history.models import (
    CURRENT_HISTORY_STATE_SCHEMA_VERSION,
    HistoryNextAction,
    HistoryOperationState,
    HistoryPhase,
)
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document
from git_stage_batch.history.state import (
    active_history_operation_id,
    history_operation_directory,
    history_output_ref,
    history_recovery_ref,
    initialize_history_operation,
    inspect_history_operation,
    load_active_history_operation,
    update_history_operation,
)
from git_stage_batch.utils.paths import get_rewrite_state_directory_path

from .conftest import git


def _prepared_state(repo, operation_id: str = "a" * 32):
    document = acquire_history_plan_document(repo.base)
    plan_record = history_plan_document_record(document)
    recovery_ref = history_recovery_ref(operation_id)
    git("update-ref", recovery_ref, repo.tip)
    state = HistoryOperationState(
        schema_version=CURRENT_HISTORY_STATE_SCHEMA_VERSION,
        operation_id=operation_id,
        phase=HistoryPhase.PREPARED,
        next_action=HistoryNextAction.BUILD_OUTPUT,
        plan_sha256=history_json_sha256(plan_record),
        object_format=document.snapshot.object_format,
        branch_ref="refs/heads/topic",
        base_commit=repo.base,
        original_tip=repo.tip,
        original_final_tree=document.snapshot.final_tree,
        source_commits=tuple(
            commit.commit_id for commit in document.snapshot.commits
        ),
        allowed_remote_refs=(),
        recovery_ref=recovery_ref,
        output_ref=history_output_ref(operation_id),
        expected_branch_tip=repo.tip,
        planned_output_count=len(document.plan.outputs),
        output_commits=(),
        completed_output_count=0,
        last_verified_commit=None,
        last_verified_tree=None,
        diagnostic=None,
    )
    return state, document


def test_initialize_publishes_private_recoverable_checkpoint(
    linear_history_repo,
):
    state, document = _prepared_state(linear_history_repo)

    initialize_history_operation(state, document)

    assert active_history_operation_id() == state.operation_id
    assert load_active_history_operation() == state
    operation_directory = history_operation_directory(state.operation_id)
    assert stat.S_IMODE(operation_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((operation_directory / "plan.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((operation_directory / "state.json").stat().st_mode) == 0o600
    assert inspect_history_operation(state).resume_ready is True


def test_initialize_requires_existing_exact_recovery_ref(linear_history_repo):
    document = acquire_history_plan_document(linear_history_repo.base)
    plan_record = history_plan_document_record(document)
    state = HistoryOperationState(
        schema_version=1,
        operation_id="b" * 32,
        phase=HistoryPhase.PREPARED,
        next_action=HistoryNextAction.BUILD_OUTPUT,
        plan_sha256=history_json_sha256(plan_record),
        object_format=document.snapshot.object_format,
        branch_ref="refs/heads/topic",
        base_commit=linear_history_repo.base,
        original_tip=linear_history_repo.tip,
        original_final_tree=document.snapshot.final_tree,
        source_commits=tuple(
            commit.commit_id for commit in document.snapshot.commits
        ),
        allowed_remote_refs=(),
        recovery_ref=history_recovery_ref("b" * 32),
        output_ref=history_output_ref("b" * 32),
        expected_branch_tip=linear_history_repo.tip,
        planned_output_count=len(document.plan.outputs),
        output_commits=(),
        completed_output_count=0,
        last_verified_commit=None,
        last_verified_tree=None,
        diagnostic=None,
    )

    with pytest.raises(CommandError, match="recovery_ref must already"):
        initialize_history_operation(state, document)


def test_update_accepts_only_closed_phase_transitions(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    initialize_history_operation(state, document)

    building = replace(state, phase=HistoryPhase.BUILDING)
    update_history_operation(building)

    assert load_active_history_operation() == building
    invalid = replace(
        building,
        phase=HistoryPhase.COMPLETE,
        next_action=HistoryNextAction.NONE,
    )
    with pytest.raises(CommandError, match="phase transition"):
        update_history_operation(invalid)


def test_inspection_detects_plan_tampering(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    initialize_history_operation(state, document)
    plan_path = history_operation_directory(state.operation_id) / "plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")

    inspection = inspect_history_operation(state)

    assert inspection.plan_matches is False
    assert inspection.resume_ready is False
    assert "plan-changed" in inspection.blockers


def test_inspection_detects_external_branch_movement(linear_history_repo):
    repo = linear_history_repo
    state, document = _prepared_state(repo)
    initialize_history_operation(state, document)
    git("update-ref", "refs/heads/topic", repo.first, repo.tip)

    inspection = inspect_history_operation(state)

    assert inspection.branch_ref_matches is True
    assert inspection.branch_tip_matches is False
    assert "branch-tip-changed" in inspection.blockers


def test_status_reports_exact_next_action_and_recovery(
    linear_history_repo,
    capsys,
):
    state, document = _prepared_state(linear_history_repo)
    initialize_history_operation(state, document)

    command_rewrite_status(porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["active"] is True
    assert output["phase"] == "PREPARED"
    assert output["next_action"] == "BUILD_OUTPUT"
    assert output["recovery_ref"] == state.recovery_ref
    assert output["output_ref"] == state.output_ref
    assert output["progress"]["planned_output_count"] == 2
    assert output["inspection"]["resume_ready"] is True
    assert output["manual_recovery_command"].startswith("git update-ref refs/heads/topic")


def test_status_reports_no_active_operation(linear_history_repo, capsys):
    command_rewrite_status(porcelain=True)

    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "operation": "rewrite-status",
        "active": False,
    }


def test_active_pointer_must_not_be_a_symlink(linear_history_repo):
    history_directory = get_rewrite_state_directory_path()
    history_directory.mkdir(parents=True)
    (history_directory / "active").symlink_to("missing")

    with pytest.raises(CommandError, match="active must be a regular file"):
        active_history_operation_id()


def test_history_directory_must_not_be_a_symlink(linear_history_repo):
    history_directory = get_rewrite_state_directory_path()
    history_directory.parent.mkdir(parents=True, exist_ok=True)
    target = linear_history_repo.root / "redirected-history"
    target.mkdir()
    history_directory.symlink_to(target, target_is_directory=True)

    with pytest.raises(CommandError, match="history state path must be a directory"):
        active_history_operation_id()


def test_operation_directory_must_not_be_a_symlink(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    initialize_history_operation(state, document)
    operation_directory = history_operation_directory(state.operation_id)
    relocated = operation_directory.with_name("relocated")
    operation_directory.rename(relocated)
    operation_directory.symlink_to(relocated, target_is_directory=True)

    with pytest.raises(CommandError, match="operation state path must be a directory"):
        load_active_history_operation()


def test_initialize_rejects_a_symbolic_recovery_ref(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    git("symbolic-ref", state.recovery_ref, state.branch_ref)

    with pytest.raises(CommandError, match="recovery_ref must already"):
        initialize_history_operation(state, document)


def test_initialize_rejects_any_existing_output_ref(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    tree = git("rev-parse", "HEAD^{tree}")
    git("update-ref", state.output_ref, tree)

    with pytest.raises(CommandError, match="output_ref must not exist"):
        initialize_history_operation(state, document)


def test_inspection_binds_state_facts_to_persisted_plan(linear_history_repo):
    repo = linear_history_repo
    state, document = _prepared_state(repo)
    initialize_history_operation(state, document)
    forged = replace(state, source_commits=(repo.base, repo.tip))

    inspection = inspect_history_operation(forged)

    assert inspection.plan_matches is False
    assert "plan-changed" in inspection.blockers
