"""End-to-end coverage for the namespaced fixup creation workflow."""

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


def test_fixup_create_cli_makes_reviewable_commit(functional_repo):
    source = functional_repo / "src" / "utils.py"
    base = _git("rev-parse", "HEAD")

    source.write_text("def helper():\n    return 84\n")
    _git("add", "src/utils.py")
    _git("commit", "-m", "Adjust helper value")
    target = _git("rev-parse", "HEAD")

    source.write_text("def helper():\n    return 126\n")
    _git("add", "src/utils.py")

    result = git_stage_batch(
        "fixup",
        "create",
        base,
        "--porcelain",
    )
    output = json.loads(result.stdout)

    assert output["operation"] == "fixup-create"
    assert output["groups"][0]["target"] == target
    assert output["summary"]["created_commits"] == 1
    assert _git("log", "-1", "--format=%s") == "fixup! Adjust helper value"
    assert _git("diff", "--cached") == ""


def test_fixup_create_cli_replays_a_reviewed_plan(functional_repo):
    source = functional_repo / "src" / "utils.py"
    base = _git("rev-parse", "HEAD")
    source.write_text("def helper():\n    return 84\n")
    _git("add", "src/utils.py")
    _git("commit", "-m", "Adjust helper value")
    source.write_text("def helper():\n    return 126\n")
    _git("add", "src/utils.py")

    dry_run = git_stage_batch(
        "fixup",
        "create",
        base,
        "--dry-run",
        "--porcelain",
    )
    plan_path = functional_repo / "fixup-plan.json"
    plan_path.write_text(dry_run.stdout, encoding="utf-8")
    result = git_stage_batch(
        "fixup",
        "create",
        "--plan",
        str(plan_path),
        "--porcelain",
    )
    output = json.loads(result.stdout)

    assert output["assignments"][0]["basis"] == "automatic"
    assert output["summary"]["created_commits"] == 1
    assert _git("log", "-1", "--format=%s") == "fixup! Adjust helper value"
    assert _git("diff", "--cached") == ""


def test_fixup_create_supports_sha256_repositories(tmp_path, monkeypatch):
    repo = tmp_path / "sha256-repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    initialized = subprocess.run(
        ["git", "init", "--object-format=sha256"],
        capture_output=True,
        text=True,
    )
    if initialized.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")

    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    source = repo / "value.txt"
    source.write_text("base\n")
    _git("add", "value.txt")
    _git("commit", "-m", "Base")
    base = _git("rev-parse", "HEAD")

    source.write_text("topic\n")
    _git("commit", "-am", "Change value")
    source.write_text("fixed\n")
    _git("add", "value.txt")

    dry_run = git_stage_batch(
        "fixup",
        "create",
        base,
        "--dry-run",
        "--porcelain",
    )
    plan_path = repo / "fixup-plan.json"
    plan_path.write_text(dry_run.stdout, encoding="utf-8")
    result = git_stage_batch(
        "fixup",
        "create",
        "--plan",
        str(plan_path),
        "--porcelain",
    )
    output = json.loads(result.stdout)

    assert output["source"]["object_format"] == "sha256"
    assert len(output["range"]["head"]) == 64
    assert len(output["groups"][0]["created_commit"]) == 64
