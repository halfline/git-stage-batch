"""Tests for strict reusable history-plan validation."""

from __future__ import annotations

import json

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.history.plan_files import read_and_validate_history_plan
from git_stage_batch.history.records import history_plan_document_record
from git_stage_batch.history.scan import acquire_history_plan_document

from .conftest import git


def _write_plan(path, plan: dict[str, object]) -> None:
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def _plan(repo, path) -> dict[str, object]:
    record = history_plan_document_record(
        acquire_history_plan_document(repo.base)
    )
    _write_plan(path, record)
    return record


def test_validate_accepts_unchanged_keep_plan(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    original = _plan(repo, path)

    validated = read_and_validate_history_plan(str(path))

    assert validated.snapshot.tip_commit == repo.tip
    assert [output.operation for output in validated.plan.outputs] == [
        "KEEP",
        "KEEP",
    ]
    assert history_plan_document_record(validated)["snapshot"] == original["snapshot"]


def test_validate_ignores_json_object_key_order(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["snapshot"] = dict(reversed(tuple(plan["snapshot"].items())))
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.snapshot.tip_commit == repo.tip


def test_validate_accepts_message_only_reword(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "Explain alpha better\n"
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.plan.outputs[0].operation == "REWORD"
    assert validated.plan.outputs[0].message == "Explain alpha better\n"
    assert validated.snapshot.final_tree == git("rev-parse", "HEAD^{tree}")


def test_validate_rejects_message_edit_marked_keep(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["message"] = "Forged keep\n"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="without a REWORD"):
        read_and_validate_history_plan(str(path))


def test_validate_requires_reword_for_encoding_change(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["encoding"] = "ISO-8859-1"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="encoding changed without a REWORD"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_unencodable_reword(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "snowman ☃\n"
    plan["plan"]["outputs"][0]["encoding"] = "ascii"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="cannot be encoded as ascii"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_omitted_patch_unit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["unit_ids"] = []
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="exactly conserve"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_duplicate_patch_unit(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    units = plan["plan"]["outputs"][0]["unit_ids"]
    plan["plan"]["outputs"][0]["unit_ids"] = [units[0], units[0]]
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="exactly conserve"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_reordered_source_commits(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"].reverse()
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="consume the next source commit"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_forged_snapshot_fact(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["snapshot"]["commits"][0]["patch"]["units"][0]["path"] = "forged.txt"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="immutable range.*changed"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_stale_tip(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    _plan(repo, path)
    repo.source.write_text("new tip\n", encoding="utf-8")
    git("commit", "-am", "Move tip")

    with pytest.raises(CommandError, match="immutable range.*changed"):
        read_and_validate_history_plan(str(path))


def test_validate_recalculates_informational_safety(linear_history_repo):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["safety"] = {"forged": True}
    _write_plan(path, plan)

    validated = read_and_validate_history_plan(str(path))

    assert validated.safety.mutation_ready is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(
            '"operation": "rewrite-plan"',
            '"operation": "rewrite-plan", "operation": "rewrite-plan"',
            1,
        ),
        lambda payload: payload.replace('"schema_version": 1', '"schema_version": NaN', 1),
        lambda payload: payload.replace(
            '"operation": "rewrite-plan"',
            '"operation": "rewrite-plan", "unknown": true',
            1,
        ),
    ],
)
def test_validate_rejects_non_strict_json(linear_history_repo, mutation):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    payload = mutation(json.dumps(plan))
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(CommandError, match="Invalid rewrite plan"):
        read_and_validate_history_plan(str(path))


def test_validate_rejects_future_operation_before_executor_support(
    linear_history_repo,
):
    repo = linear_history_repo
    path = repo.root / "plan.json"
    plan = _plan(repo, path)
    plan["plan"]["outputs"][0]["operation"] = "INTEGRATE"
    _write_plan(path, plan)

    with pytest.raises(CommandError, match="KEEP.*REWORD"):
        read_and_validate_history_plan(str(path))
