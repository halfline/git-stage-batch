"""Tests for the bundled decompose workflow checkpoint helper."""

from __future__ import annotations

import importlib.util
import subprocess
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
