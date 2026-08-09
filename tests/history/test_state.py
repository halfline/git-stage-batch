"""Tests for durable history-operation checkpoints."""

from __future__ import annotations

import json
import stat
from dataclasses import replace

import pytest

import git_stage_batch.history.state as history_state
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
    activate_prepared_history_operation,
    history_operation_directory,
    history_output_ref,
    history_recovery_ref,
    inspect_history_operation,
    load_active_history_operation,
    prepare_history_operation,
    publish_prepared_history_operation,
    update_history_operation,
)
from git_stage_batch.output.rewrite_operation import print_rewrite_operation
from git_stage_batch.output.rewrite_status import print_rewrite_status
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
        resolution_raw_plan_sha256=None,
        resolution_complete_sha256=None,
        object_format=document.snapshot.object_format,
        branch_ref="refs/heads/topic",
        base_commit=repo.base,
        original_tip=repo.tip,
        original_final_tree=document.snapshot.final_tree,
        source_commits=tuple(commit.commit_id for commit in document.snapshot.commits),
        allowed_remote_refs=(),
        recovery_ref=recovery_ref,
        output_ref=history_output_ref(operation_id),
        expected_branch_tip=repo.tip,
        planned_output_count=len(document.plan.outputs),
        output_commits=(),
        completed_output_count=0,
        pending_output_commit=None,
        pending_output_tree=None,
        last_verified_commit=None,
        last_verified_tree=None,
        verification_sha256=None,
        diagnostic=None,
    )
    return state, document


def _initialize_history_operation(state, document) -> None:
    preparation = prepare_history_operation(state, document)
    publish_prepared_history_operation(state, preparation)
    activate_prepared_history_operation(state)


def _state_in_phase(
    state: HistoryOperationState,
    phase: HistoryPhase,
) -> HistoryOperationState:
    if phase is HistoryPhase.PREPARED:
        return state
    if phase is HistoryPhase.PAUSED:
        return replace(
            state,
            phase=phase,
            diagnostic="paused before output construction",
        )
    if phase is HistoryPhase.COMPLETE:
        return replace(
            state,
            phase=phase,
            next_action=HistoryNextAction.NONE,
            expected_branch_tip=state.original_tip,
            output_commits=state.source_commits,
            completed_output_count=state.planned_output_count,
            last_verified_commit=state.original_tip,
            last_verified_tree=state.original_final_tree,
            verification_sha256="f" * 64,
        )
    raise AssertionError(f"unsupported test phase: {phase.value}")


@pytest.mark.parametrize(
    "phase",
    [
        HistoryPhase.PREPARED,
        HistoryPhase.PAUSED,
        HistoryPhase.COMPLETE,
    ],
)
def test_schema_two_state_round_trips_without_schema_three_fields(
    linear_history_repo,
    phase,
):
    state, _document = _prepared_state(linear_history_repo)
    legacy = replace(_state_in_phase(state, phase), schema_version=2)
    record = history_state._state_record(legacy)

    decoded = history_state._decode_state(json.dumps(record))

    assert decoded == legacy
    assert decoded.resolution_raw_plan_sha256 is None
    assert decoded.resolution_complete_sha256 is None
    assert "resolution_raw_plan_sha256" not in record
    assert "resolution_complete_sha256" not in record
    assert history_state._state_record(decoded) == record


def test_schema_three_state_round_trips_resolution_provenance(
    linear_history_repo,
):
    state, _document = _prepared_state(linear_history_repo)
    state = replace(
        state,
        resolution_raw_plan_sha256="b" * 64,
        resolution_complete_sha256="c" * 64,
    )

    record = history_state._state_record(state)
    decoded = history_state._decode_state(json.dumps(record))

    assert state.schema_version == 3
    assert record["resolution_raw_plan_sha256"] == "b" * 64
    assert record["resolution_complete_sha256"] == "c" * 64
    assert decoded == state


def test_state_schema_dispatch_rejects_hybrid_records(linear_history_repo):
    state, _document = _prepared_state(linear_history_repo)
    legacy_record = history_state._state_record(replace(state, schema_version=2))
    legacy_record["resolution_raw_plan_sha256"] = None
    legacy_record["resolution_complete_sha256"] = None

    with pytest.raises(CommandError, match="unknown field"):
        history_state._decode_state(json.dumps(legacy_record))

    current_record = history_state._state_record(state)
    del current_record["resolution_complete_sha256"]
    with pytest.raises(CommandError, match="missing field"):
        history_state._decode_state(json.dumps(current_record))


@pytest.mark.parametrize(
    ("raw_plan_sha256", "complete_sha256", "expected_error"),
    [
        ("b" * 64, None, "both be null or both be set"),
        ("B" * 64, "c" * 64, "resolution_raw_plan_sha256 must be lowercase"),
        ("b" * 64, "short", "resolution_complete_sha256 must be lowercase"),
    ],
)
def test_schema_three_rejects_invalid_resolution_provenance(
    linear_history_repo,
    raw_plan_sha256,
    complete_sha256,
    expected_error,
):
    state, _document = _prepared_state(linear_history_repo)
    record = history_state._state_record(state)
    record["resolution_raw_plan_sha256"] = raw_plan_sha256
    record["resolution_complete_sha256"] = complete_sha256

    with pytest.raises(CommandError, match=expected_error):
        history_state._decode_state(json.dumps(record))


def test_initialize_publishes_private_recoverable_checkpoint(
    linear_history_repo,
):
    state, document = _prepared_state(linear_history_repo)

    _initialize_history_operation(state, document)

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
        schema_version=CURRENT_HISTORY_STATE_SCHEMA_VERSION,
        operation_id="b" * 32,
        phase=HistoryPhase.PREPARED,
        next_action=HistoryNextAction.BUILD_OUTPUT,
        plan_sha256=history_json_sha256(plan_record),
        resolution_raw_plan_sha256=None,
        resolution_complete_sha256=None,
        object_format=document.snapshot.object_format,
        branch_ref="refs/heads/topic",
        base_commit=linear_history_repo.base,
        original_tip=linear_history_repo.tip,
        original_final_tree=document.snapshot.final_tree,
        source_commits=tuple(commit.commit_id for commit in document.snapshot.commits),
        allowed_remote_refs=(),
        recovery_ref=history_recovery_ref("b" * 32),
        output_ref=history_output_ref("b" * 32),
        expected_branch_tip=linear_history_repo.tip,
        planned_output_count=len(document.plan.outputs),
        output_commits=(),
        completed_output_count=0,
        pending_output_commit=None,
        pending_output_tree=None,
        last_verified_commit=None,
        last_verified_tree=None,
        verification_sha256=None,
        diagnostic=None,
    )

    with pytest.raises(CommandError, match="recovery_ref must"):
        _initialize_history_operation(state, document)


def test_update_accepts_only_closed_phase_transitions(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    _initialize_history_operation(state, document)

    building = replace(state, phase=HistoryPhase.BUILDING)
    update_history_operation(building)

    assert load_active_history_operation() == building
    invalid = replace(
        building,
        phase=HistoryPhase.COMPLETE,
        next_action=HistoryNextAction.NONE,
    )
    with pytest.raises(CommandError, match="requires every output commit"):
        update_history_operation(invalid)


def test_inspection_detects_plan_tampering(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    _initialize_history_operation(state, document)
    plan_path = history_operation_directory(state.operation_id) / "plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")

    inspection = inspect_history_operation(state)

    assert inspection.plan_matches is False
    assert inspection.plan_operation_counts == ()
    assert inspection.resume_ready is False
    assert "plan-changed" in inspection.blockers


def test_inspection_detects_external_branch_movement(linear_history_repo):
    repo = linear_history_repo
    state, document = _prepared_state(repo)
    _initialize_history_operation(state, document)
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
    document = replace(
        document,
        plan=replace(
            document.plan,
            outputs=(
                replace(document.plan.outputs[0], operation="REWORD"),
                document.plan.outputs[1],
            ),
        ),
    )
    state = replace(
        state,
        plan_sha256=history_json_sha256(history_plan_document_record(document)),
    )
    _initialize_history_operation(state, document)

    command_rewrite_status(porcelain=True)

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == 1
    assert output["active"] is True
    assert output["phase"] == "PREPARED"
    assert output["next_action"] == "BUILD_OUTPUT"
    assert output["recovery_ref"] == state.recovery_ref
    assert output["output_ref"] == state.output_ref
    assert output["plan"]["operation_counts"] == {"KEEP": 1, "REWORD": 1}
    assert output["progress"]["planned_output_count"] == 2
    assert output["inspection"]["resume_ready"] is True
    assert output["manual_recovery_command"].startswith(
        "git update-ref refs/heads/topic"
    )


def test_status_shell_quotes_manual_recovery_branch(
    linear_history_repo,
    capsys,
):
    state, document = _prepared_state(linear_history_repo)
    _initialize_history_operation(state, document)
    inspection = inspect_history_operation(state)
    rendered_state = replace(
        state,
        branch_ref="refs/heads/topic;echo-owned",
    )

    print_rewrite_status(
        rendered_state,
        inspection,
        active=True,
        porcelain=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["manual_recovery_command"] == (
        "git update-ref 'refs/heads/topic;echo-owned' "
        f"{state.recovery_ref} {state.expected_branch_tip}"
    )


def test_operation_output_does_not_claim_unrecorded_verification(
    linear_history_repo,
    capsys,
):
    state, _document = _prepared_state(linear_history_repo)
    paused = replace(
        state,
        phase=HistoryPhase.PAUSED,
        diagnostic="interrupted before verification",
    )

    print_rewrite_operation("continue", paused, porcelain=False)

    output = capsys.readouterr().out
    assert "Next action: BUILD_OUTPUT" in output
    assert "Verified final tree" not in output


def test_operation_output_identifies_completed_abort_as_noop(
    linear_history_repo,
    capsys,
):
    state, _document = _prepared_state(linear_history_repo)
    complete = replace(
        state,
        phase=HistoryPhase.COMPLETE,
        next_action=HistoryNextAction.NONE,
        expected_branch_tip=state.original_tip,
        output_commits=state.source_commits,
        completed_output_count=state.planned_output_count,
        last_verified_commit=state.original_tip,
        last_verified_tree=state.original_final_tree,
        verification_sha256="f" * 64,
    )

    print_rewrite_operation("abort", complete, porcelain=False)

    output = capsys.readouterr().out
    assert "already complete; abort made no changes" in output
    assert "Verified final tree" not in output


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
    _initialize_history_operation(state, document)
    operation_directory = history_operation_directory(state.operation_id)
    relocated = operation_directory.with_name("relocated")
    operation_directory.rename(relocated)
    operation_directory.symlink_to(relocated, target_is_directory=True)

    with pytest.raises(CommandError, match="operation state path must be a directory"):
        load_active_history_operation()


def test_initialize_rejects_a_symbolic_recovery_ref(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    git("symbolic-ref", state.recovery_ref, state.branch_ref)

    with pytest.raises(CommandError, match="recovery_ref must"):
        _initialize_history_operation(state, document)


def test_initialize_rejects_any_existing_output_ref(linear_history_repo):
    state, document = _prepared_state(linear_history_repo)
    tree = git("rev-parse", "HEAD^{tree}")
    git("update-ref", state.output_ref, tree)

    with pytest.raises(CommandError, match="output_ref must not exist"):
        _initialize_history_operation(state, document)


def test_inspection_binds_state_facts_to_persisted_plan(linear_history_repo):
    repo = linear_history_repo
    state, document = _prepared_state(repo)
    _initialize_history_operation(state, document)
    forged = replace(state, source_commits=(repo.base, repo.tip))

    inspection = inspect_history_operation(forged)

    assert inspection.plan_matches is False
    assert inspection.plan_operation_counts == ()
    assert "plan-changed" in inspection.blockers


def test_inspection_binds_plan_digest_and_facts_to_one_read(
    linear_history_repo,
    monkeypatch,
):
    state, document = _prepared_state(linear_history_repo)
    _initialize_history_operation(state, document)
    original_read = history_state.read_required_text_file_contents_and_sha256
    read_count = 0

    def replace_plan_after_read(path):
        nonlocal read_count
        read_count += 1
        result = original_read(path)
        path.write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(
        history_state,
        "read_required_text_file_contents_and_sha256",
        replace_plan_after_read,
    )

    inspection = inspect_history_operation(state)

    assert read_count == 1
    assert inspection.plan_matches is True
    assert inspection.plan_operation_counts == (("KEEP", 2),)
