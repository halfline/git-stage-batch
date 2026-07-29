"""Tests for persisted unified-diff context configuration."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.utils.context_lines import get_context_lines
from git_stage_batch.utils.paths import get_context_lines_file_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary Git repository."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    monkeypatch.chdir(repo)
    return repo


def test_get_context_lines_default_does_not_create_state(temp_git_repo):
    """Reading an absent setting should leave repository state untouched."""
    assert get_context_lines() == 3
    assert not (temp_git_repo / ".git" / "git-stage-batch").exists()


def test_get_context_lines_reads_file(temp_git_repo):
    """A stored integer should override the default."""
    context_file = get_context_lines_file_path()
    context_file.parent.mkdir(parents=True)
    context_file.write_text("5\n")

    assert get_context_lines() == 5


def test_get_context_lines_invalid_content(temp_git_repo):
    """Malformed stored content should use the default."""
    context_file = get_context_lines_file_path()
    context_file.parent.mkdir(parents=True)
    context_file.write_text("not-a-number\n")

    assert get_context_lines() == 3
