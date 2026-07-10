"""End-to-end workflows in repositories using SHA-256 object IDs."""

from __future__ import annotations

import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sha256_repo(tmp_path, monkeypatch):
    """Create a committed SHA-256 repository."""
    repo = tmp_path / "sha256-repo"
    result = subprocess.run(
        ["git", "init", "--object-format=sha256", str(repo)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"installed Git lacks SHA-256 repository support: {result.stderr}")
    monkeypatch.chdir(repo)
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    (repo / "README.md").write_text("one\n")
    _git("add", "README.md")
    _git("commit", "-m", "Initial commit")
    assert _git("rev-parse", "--show-object-format").stdout.strip() == "sha256"
    return repo


def test_new_empty_file_can_be_selected_and_staged(sha256_repo):
    """SHA-256 empty-blob metadata should produce an empty-file selection."""
    empty_file = sha256_repo / "empty.txt"
    empty_file.write_bytes(b"")

    git_stage_batch("start")
    git_stage_batch("include", "--file", "empty.txt")

    assert _git("diff", "--cached", "--name-only").stdout == "empty.txt\n"


def test_intent_to_add_survives_start_and_abort(sha256_repo):
    """Intent-to-add detection should not depend on SHA-1's empty blob ID."""
    intent_file = sha256_repo / "intent.txt"
    intent_file.write_text("new\n")
    _git("add", "-N", "intent.txt")

    git_stage_batch("start")
    entry = _git("ls-files", "--stage", "intent.txt").stdout.split()[1]
    assert len(entry) == 64
    git_stage_batch("abort")

    assert _git("status", "--porcelain", "--", "intent.txt").stdout == " A intent.txt\n"
