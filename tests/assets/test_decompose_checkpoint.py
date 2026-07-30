"""Tests for the bundled decompose workflow checkpoint helper."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_HELPER = (
    PROJECT_ROOT
    / "assets"
    / "codex-skills"
    / "decompose-and-commit-unstaged-changes"
    / "scripts"
    / "decompose-checkpoint.py"
)


def _git(repo: Path, *args: str) -> str:
    """Run Git and return stripped stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _helper(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the Codex checkpoint helper in a test repository."""
    result = subprocess.run(
        [sys.executable, str(CODEX_HELPER), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check:
        result.check_returncode()
    return result


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a repository with a base commit and a later draft."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "commit", "--allow-empty", "-m", "Base")
    return repo


def test_resume_start_preserves_checkpoint_progress_and_base(
    git_repo: Path,
) -> None:
    """Restarting Phase 1 must retain recovery data and the canonical base."""
    base = _git(git_repo, "rev-parse", "HEAD")
    _git(git_repo, "commit", "--allow-empty", "-m", "Draft")
    _helper(git_repo, "start", "--mode", "full", "--base", base)
    _helper(
        git_repo,
        "mark",
        "--phase",
        "phase3-running",
        "--completed-batch",
        "decompose-one",
        "--commit",
        "HEAD",
    )
    checkpoint_path = (
        git_repo / ".git-stage-batch" / "decompose-checkpoint.json"
    )
    before = json.loads(checkpoint_path.read_text(encoding="utf-8"))

    _helper(git_repo, "start", "--mode", "resume")

    after = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert after["base"] == base
    assert after["mode"] == before["mode"] == "full"
    assert after["phase"] == "phase1-running"
    assert after["completed_batches"] == before["completed_batches"]
    assert after["commits"] == before["commits"]
    assert after["events"][:-1] == before["events"]
    assert after["events"][-1]["previous_phase"] == "phase3-running"
    status = json.loads(
        _helper(git_repo, "status", "--json").stdout
    )
    assert status["resume_target"] == "phase1"

    saved = checkpoint_path.read_bytes()
    mismatch = _helper(
        git_repo,
        "start",
        "--mode",
        "resume",
        "--base",
        "HEAD",
        check=False,
    )
    assert mismatch.returncode != 0
    assert "resume base does not match" in mismatch.stderr
    assert checkpoint_path.read_bytes() == saved


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", True),
        ("base", "HEAD"),
        ("mode", 1),
        ("phase", None),
        ("phase", "unknown"),
        ("events", [None]),
        ("completed_batches", [1]),
        ("commits", ["not-an-object"]),
        ("commits", [{"sha": "missing-commit", "subject": ""}]),
        ("current_batch", ""),
    ],
)
def test_resume_rejects_invalid_checkpoint_structure(
    git_repo: Path,
    field: str,
    value: object,
) -> None:
    """Recovery commands must not bless malformed nested checkpoint state."""
    _helper(git_repo, "start", "--mode", "full", "--base", "HEAD")
    checkpoint_path = (
        git_repo / ".git-stage-batch" / "decompose-checkpoint.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint[field] = value
    checkpoint_path.write_text(
        json.dumps(checkpoint),
        encoding="utf-8",
    )
    saved = checkpoint_path.read_bytes()

    resume = _helper(
        git_repo,
        "start",
        "--mode",
        "resume",
        check=False,
    )

    assert resume.returncode != 0
    assert "invalid decompose checkpoint" in resume.stderr
    assert checkpoint_path.read_bytes() == saved


def test_fresh_start_validates_base_before_clearing_state(
    git_repo: Path,
) -> None:
    """An invalid base must not erase an existing recovery checkpoint."""
    _helper(git_repo, "start", "--mode", "full", "--base", "HEAD")
    state_dir = git_repo / ".git-stage-batch"
    checkpoint_path = state_dir / "decompose-checkpoint.json"
    plan_path = state_dir / "decompose-plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    saved_checkpoint = checkpoint_path.read_bytes()

    result = _helper(
        git_repo,
        "start",
        "--mode",
        "full",
        "--base",
        "missing-base",
        check=False,
    )

    assert result.returncode != 0
    assert "invalid decompose base revision" in result.stderr
    assert checkpoint_path.read_bytes() == saved_checkpoint
    assert plan_path.read_text(encoding="utf-8") == "{}\n"


def test_failed_atomic_replace_preserves_checkpoint(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed checkpoint replacement must leave prior recovery data intact."""
    state_dir = git_repo / ".git-stage-batch"
    monkeypatch.setenv("DECOMPOSE_STATE_DIR", str(state_dir))
    monkeypatch.chdir(git_repo)
    spec = importlib.util.spec_from_file_location(
        "decompose_checkpoint_for_test",
        CODEX_HELPER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = {
        "schema": 1,
        "base": _git(git_repo, "rev-parse", "HEAD"),
        "phase": "started",
        "events": [],
        "completed_batches": [],
        "commits": [],
    }
    module.save_checkpoint(first)
    checkpoint_path = state_dir / "decompose-checkpoint.json"
    saved = checkpoint_path.read_bytes()

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected replacement failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    changed = {**first, "phase": "phase1-running"}

    with pytest.raises(OSError, match="injected replacement failure"):
        module.save_checkpoint(changed)

    assert checkpoint_path.read_bytes() == saved
    assert not list(state_dir.glob(".decompose-checkpoint.json.*.tmp"))
