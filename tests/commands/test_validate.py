"""Tests for non-mutating batch metadata diagnostics."""

from __future__ import annotations

import json
import subprocess

import pytest

from git_stage_batch.commands.validate import command_validate_batches
from git_stage_batch.commands.new import command_new_batch
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import get_batch_metadata_file_path
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)
    return tmp_path


def test_validate_reports_current_metadata_without_mutation(temp_git_repo, capsys):
    command_new_batch("current")
    before = subprocess.run(
        ["git", "rev-parse", "refs/git-stage-batch/state/current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)
    assert report["batches"][0]["status"] == "ok"
    assert report["batches"][0]["schema_version"] == 1
    after = subprocess.run(
        ["git", "rev-parse", "refs/git-stage-batch/state/current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert after == before


def test_validate_previews_legacy_migration(temp_git_repo, capsys):
    subprocess.run(["git", "update-ref", "refs/batches/legacy", "HEAD"], check=True)
    metadata_path = get_batch_metadata_file_path("legacy")
    metadata_path.parent.mkdir(parents=True)
    write_text_file_contents(
        metadata_path,
        json.dumps({
            "note": "Legacy",
            "created_at": "",
            "baseline": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "files": {},
        }),
    )

    command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["status"] == "ok"
    assert report["migration_required"] is True
    assert json.loads(metadata_path.read_text()).get("schema_version") is None


def test_validate_reports_invalid_orphaned_legacy_name(temp_git_repo, capsys):
    metadata_path = get_batch_metadata_file_path("invalid^name")
    metadata_path.parent.mkdir(parents=True)
    write_text_file_contents(metadata_path, "{}")

    with pytest.raises(CommandError):
        command_validate_batches(porcelain=True)

    report = json.loads(capsys.readouterr().out)["batches"][0]
    assert report["status"] == "error"
    assert "Git ref naming rules" in report["errors"][0]
