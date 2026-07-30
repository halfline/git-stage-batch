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
def test_resume_and_status_reject_invalid_checkpoint_structure(
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
    status = _helper(git_repo, "status", "--json", check=False)

    assert resume.returncode != 0
    assert status.returncode != 0
    assert "invalid decompose checkpoint" in resume.stderr
    assert "invalid decompose checkpoint" in status.stderr
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


def test_mark_validates_commit_before_replacing_checkpoint(
    git_repo: Path,
) -> None:
    """A mark must not record a revision that does not identify a commit."""
    _helper(git_repo, "start", "--mode", "full", "--base", "HEAD")
    checkpoint_path = (
        git_repo / ".git-stage-batch" / "decompose-checkpoint.json"
    )
    saved = checkpoint_path.read_bytes()

    result = _helper(
        git_repo,
        "mark",
        "--phase",
        "phase3-running",
        "--commit",
        "missing-commit",
        check=False,
    )

    assert result.returncode != 0
    assert "invalid decompose commit revision" in result.stderr
    assert checkpoint_path.read_bytes() == saved


def test_start_refuses_symlinked_state_directory(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    """Fresh cleanup must not follow a workflow-state directory symlink."""
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    (git_repo / ".git-stage-batch").symlink_to(
        outside,
        target_is_directory=True,
    )

    state_dir_result = _helper(
        git_repo,
        "state-dir",
        check=False,
    )
    result = _helper(
        git_repo,
        "start",
        "--mode",
        "full",
        "--base",
        "HEAD",
        check=False,
    )

    assert state_dir_result.returncode != 0
    assert result.returncode != 0
    assert (
        "refusing to use symlinked state path"
        in state_dir_result.stderr
    )
    assert "refusing to use symlinked state path" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_fresh_start_clears_only_decompose_state(git_repo: Path) -> None:
    """Fresh decompose state must not delete sibling workflow recovery."""
    state_dir = git_repo / ".git-stage-batch"
    refine_history = state_dir / "refine-history"
    refine_messages = state_dir / "refine-commit-messages"
    refine_history.mkdir(parents=True)
    refine_messages.mkdir()
    (refine_history / "checkpoint.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (refine_messages / "checkpoint.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    unrelated = state_dir / "keep.txt"
    unrelated.write_text("keep\n", encoding="utf-8")
    for name in (
        "decompose-plan.json",
        "decompose-plan.candidate.json",
        "decompose-narrative.md",
        "decompose-refinement.md",
        ".decompose-checkpoint.json.abandoned.tmp",
    ):
        (state_dir / name).write_text("stale\n", encoding="utf-8")

    _helper(git_repo, "start", "--mode", "full", "--base", "HEAD")

    assert refine_history.is_dir()
    assert refine_messages.is_dir()
    assert unrelated.read_text(encoding="utf-8") == "keep\n"
    assert (state_dir / "decompose-checkpoint.json").is_file()
    assert not (state_dir / "decompose-plan.json").exists()
    assert not (state_dir / "decompose-plan.candidate.json").exists()
    assert not (state_dir / "decompose-narrative.md").exists()
    assert not (state_dir / "decompose-refinement.md").exists()
    assert not list(state_dir.glob(".decompose-checkpoint.json.*.tmp"))


@pytest.mark.parametrize("contents", [None, "", "{broken", "[]"])
def test_mark_requires_valid_existing_checkpoint(
    git_repo: Path,
    contents: str | None,
) -> None:
    """Mark must not invent recovery state when its checkpoint is unavailable."""
    checkpoint_path = (
        git_repo / ".git-stage-batch" / "decompose-checkpoint.json"
    )
    if contents is not None:
        checkpoint_path.parent.mkdir()
        checkpoint_path.write_text(contents, encoding="utf-8")

    result = _helper(
        git_repo,
        "mark",
        "--phase",
        "phase1-candidate",
        check=False,
    )
    status = _helper(git_repo, "status", "--json", check=False)

    assert result.returncode != 0
    if contents is None:
        assert "no decompose checkpoint" in result.stderr
        assert not checkpoint_path.exists()
        assert status.returncode == 0
    else:
        assert "invalid decompose checkpoint" in result.stderr
        assert status.returncode != 0
        assert "invalid decompose checkpoint" in status.stderr
        assert checkpoint_path.read_text(encoding="utf-8") == contents


def test_status_does_not_resume_orphaned_artifacts(git_repo: Path) -> None:
    """Artifacts without a checkpoint cannot establish a recovery base."""
    state_dir = git_repo / ".git-stage-batch"
    state_dir.mkdir()
    (state_dir / "decompose-plan.candidate.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    (state_dir / "decompose-narrative.md").write_text(
        "Draft\n",
        encoding="utf-8",
    )

    status = json.loads(
        _helper(git_repo, "status", "--json").stdout
    )

    assert status["checkpoint_exists"] is False
    assert status["candidate_exists"] is True
    assert status["narrative_exists"] is True
    assert status["resume_target"] == "fresh"


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
