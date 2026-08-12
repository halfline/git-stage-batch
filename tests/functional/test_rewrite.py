"""End-to-end rewrite scan, validation, and status coverage."""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_history_cli_scans_and_validates_reword_plan(functional_repo):
    source = functional_repo / "src" / "utils.py"
    base = _git("rev-parse", "HEAD")
    source.write_text("def helper():\n    return 84\n", encoding="utf-8")
    _git("commit", "-am", "Adjust helper")

    scan = git_stage_batch("rewrite", "scan", base, "--porcelain")
    plan = json.loads(scan.stdout)

    assert plan["operation"] == "rewrite-plan"
    assert plan["snapshot"]["range"]["base"] == base
    assert plan["snapshot"]["range"]["tip"] == _git("rev-parse", "HEAD")
    assert plan["snapshot"]["dependency_graph"]["algorithm_version"] == 1
    assert len(plan["snapshot"]["dependency_graph"]["units"]) == 1
    assert plan["safety"]["mutation_ready"] is True
    assert plan["schema_version"] == 4
    assert plan["plan"]["partitioned_units"] == []
    assert plan["plan"]["outputs"][0]["operation"] == "KEEP"
    assert plan["plan"]["outputs"][0]["materialization"] == "EXACT"
    assert "source_unit_ids" in plan["plan"]["outputs"][0]
    assert "unit_ids" not in plan["plan"]["outputs"][0]

    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "Explain helper adjustment\n"
    plan_path = functional_repo / "history-plan.json"
    plan_path.write_text(json.dumps(plan) + "\n", encoding="utf-8")
    validation = git_stage_batch(
        "rewrite",
        "validate",
        str(plan_path),
        "--porcelain",
    )
    output = json.loads(validation.stdout)

    assert output["valid"] is True
    assert output["summary"]["reworded_commits"] == 1
    assert output["summary"]["dependency_units"] == 1
    assert output["summary"]["blocked_dependencies"] == 0
    assert output["summary"]["unknown_dependencies"] == 0
    assert output["range"]["final_tree"] == _git("rev-parse", "HEAD^{tree}")


def test_history_cli_atomically_writes_plan(functional_repo):
    source = functional_repo / "README.md"
    base = _git("rev-parse", "HEAD")
    source.write_text("# Updated\n", encoding="utf-8")
    _git("commit", "-am", "Update readme")
    plan_path = functional_repo / "plans" / "history.json"

    result = git_stage_batch(
        "rewrite",
        "scan",
        base,
        "--output",
        str(plan_path),
    )

    assert "Wrote reusable rewrite plan" in result.stdout
    assert json.loads(plan_path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_history_cli_reuses_snapshot_analysis_between_processes(
    functional_repo,
    tmp_path,
    monkeypatch,
):
    cache = tmp_path / "history-snapshot-cache"
    monkeypatch.setenv("GIT_STAGE_BATCH_HISTORY_CACHE_ROOT", str(cache))
    source = functional_repo / "README.md"
    base = _git("rev-parse", "HEAD")
    source.write_text("# Cached history\n", encoding="utf-8")
    _git("commit", "-am", "Update cached history")
    plan_path = functional_repo / "history.json"

    git_stage_batch(
        "rewrite",
        "scan",
        base,
        "--output",
        str(plan_path),
    )
    cache_path = next(cache.glob("*.json"))
    first_metadata = cache_path.stat()

    git_stage_batch(
        "rewrite",
        "validate",
        str(plan_path),
        "--porcelain",
    )
    second_metadata = cache_path.stat()

    assert second_metadata.st_ino == first_metadata.st_ino
    assert second_metadata.st_mtime_ns == first_metadata.st_mtime_ns


def test_history_cli_status_without_operation(functional_repo):
    output = json.loads(
        git_stage_batch("rewrite", "status", "--porcelain").stdout
    )

    assert output == {
        "schema_version": 1,
        "operation": "rewrite-status",
        "active": False,
    }


def test_history_cli_applies_and_independently_verifies_reword_plan(
    functional_repo,
):
    source = functional_repo / "README.md"
    base = _git("rev-parse", "HEAD")
    source.write_text("# Reworded history\n", encoding="utf-8")
    _git("commit", "-am", "Old wording")
    original_tree = _git("rev-parse", "HEAD^{tree}")
    plan_path = functional_repo / "history.json"
    git_stage_batch(
        "rewrite",
        "scan",
        base,
        "--output",
        str(plan_path),
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["plan"]["outputs"][0]["operation"] = "REWORD"
    plan["plan"]["outputs"][0]["message"] = "Precise wording\n"
    plan_path.write_text(
        json.dumps(plan, indent=2) + "\n",
        encoding="utf-8",
    )

    applied = json.loads(
        git_stage_batch(
            "rewrite",
            "apply",
            str(plan_path),
            "--porcelain",
        ).stdout
    )

    assert applied["phase"] == "COMPLETE"
    assert applied["verified"] is True
    assert _git("rev-parse", "HEAD") == applied["output_tip"]
    assert _git("rev-parse", "HEAD^{tree}") == original_tree
    assert _git("show", "-s", "--format=%B", "HEAD") == "Precise wording"

    status = json.loads(
        git_stage_batch("rewrite", "status", "--porcelain").stdout
    )
    assert status["active"] is False
    assert status["available"] is True
    assert status["phase"] == "COMPLETE"
    verified = json.loads(
        git_stage_batch("rewrite", "verify", "--porcelain").stdout
    )
    assert verified["verified"] is True
    assert verified["output_tip"] == applied["output_tip"]


def test_history_execution_supports_sha256(tmp_path, monkeypatch):
    repo = tmp_path / "sha256-history"
    repo.mkdir()
    monkeypatch.chdir(repo)
    initialized = subprocess.run(
        ["git", "init", "-b", "topic", "--object-format=sha256"],
        capture_output=True,
        text=True,
    )
    if initialized.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = repo / "value.txt"
    source.write_text("base\n", encoding="utf-8")
    other = repo / "other.txt"
    other.write_text("other base\n", encoding="utf-8")
    _git("add", "value.txt", "other.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")
    source.write_text("topic\n", encoding="utf-8")
    _git("commit", "-am", "Topic")
    other.write_text("other topic\n", encoding="utf-8")
    _git("commit", "-am", "Other topic")
    original_tree = _git("rev-parse", "HEAD^{tree}")

    plan = json.loads(
        git_stage_batch("rewrite", "scan", base, "--porcelain").stdout
    )
    topic, other_topic = plan["plan"]["outputs"]
    topic["operation"] = "REWORD"
    topic["message"] = "Reword SHA-256 topic\n"
    other_topic["operation"] = "REORDER"
    plan["plan"]["outputs"] = [other_topic, topic]
    path = repo / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    validation = json.loads(
        git_stage_batch(
            "rewrite",
            "validate",
            str(path),
            "--porcelain",
        ).stdout
    )

    assert plan["snapshot"]["object_format"] == "sha256"
    assert len(plan["snapshot"]["range"]["tip"]) == 64
    assert validation["valid"] is True

    applied = json.loads(
        git_stage_batch(
            "rewrite",
            "apply",
            str(path),
            "--porcelain",
        ).stdout
    )
    verified = json.loads(
        git_stage_batch("rewrite", "verify", "--porcelain").stdout
    )

    assert applied["phase"] == "COMPLETE"
    assert len(applied["output_tip"]) == 64
    assert _git("rev-parse", "HEAD^{tree}") == original_tree
    assert _git("log", "--reverse", "--format=%s", f"{base}..HEAD").splitlines() == [
        "Other topic",
        "Reword SHA-256 topic",
    ]
    assert verified["verified"] is True
