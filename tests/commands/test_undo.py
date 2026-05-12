"""Tests for undo command."""

import os
import subprocess

import pytest

from git_stage_batch.commands.include import command_include_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.undo import command_undo


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


def _show_index_path(repo, path):
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return result.stdout


def _commit_symlink(repo, *, target):
    link_path = repo / "link"
    os.symlink(target, link_path)
    subprocess.run(["git", "add", "link"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add link"], check=True, cwd=repo, capture_output=True)
    return link_path


def test_undo_include_line_restores_symlink_worktree_snapshot(temp_git_repo):
    """Undo should restore a symlink target, not the referent bytes."""
    link_path = _commit_symlink(temp_git_repo, target="old")
    link_path.unlink()
    os.symlink("new", link_path)
    (temp_git_repo / "new").write_bytes(b"referent\n")

    command_start(quiet=True)
    command_include_line("1,2")

    command_undo(force=True)

    assert os.path.islink(link_path)
    assert os.readlink(link_path) == "new"
    assert _show_index_path(temp_git_repo, "link") == b"old"


def test_undo_include_line_restores_dangling_symlink_snapshot(temp_git_repo):
    """Undo should restore dangling symlinks as existing worktree paths."""
    link_path = _commit_symlink(temp_git_repo, target="old")
    link_path.unlink()
    os.symlink("missing", link_path)

    command_start(quiet=True)
    command_include_line("1,2")

    command_undo(force=True)

    assert os.path.islink(link_path)
    assert os.readlink(link_path) == "missing"
    assert _show_index_path(temp_git_repo, "link") == b"old"
