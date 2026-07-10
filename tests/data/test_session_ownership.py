"""Linked-worktree session ownership tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands.abort import command_abort
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.data.session import session_is_active
from git_stage_batch.data.session_ownership import require_no_foreign_session_owner
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_active_session_owner_file_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a committed repository for linked-worktree ownership tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return repo


def _add_linked_worktree(repo, path) -> None:
    subprocess.run(
        ["git", "worktree", "add", "-b", "linked-session-test", str(path)],
        check=True,
        cwd=repo,
        capture_output=True,
    )


def test_linked_worktree_cannot_start_while_main_worktree_owns_session(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """A foreign session is rejected before linked-worktree state is created."""
    worktree = tmp_path / "linked"
    _add_linked_worktree(temp_git_repo, worktree)
    (temp_git_repo / "README.md").write_text("# Test\nmain change\n")
    command_start(quiet=True)
    owner_data = json.loads(get_active_session_owner_file_path().read_text())

    monkeypatch.chdir(worktree)
    (worktree / "README.md").write_text("# Test\nlinked change\n")
    with pytest.raises(CommandError, match="Another linked worktree"):
        command_start(quiet=True)

    assert not session_is_active()
    assert json.loads(get_active_session_owner_file_path().read_text()) == owner_data

    monkeypatch.chdir(temp_git_repo)
    command_abort(quiet=True)


def test_foreign_owner_blocks_stop_and_abort(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """A linked worktree cannot clear or restore the owning session."""
    worktree = tmp_path / "linked"
    _add_linked_worktree(temp_git_repo, worktree)
    (temp_git_repo / "README.md").write_text("# Test\nmain change\n")
    command_start(quiet=True)

    monkeypatch.chdir(worktree)
    with pytest.raises(CommandError, match="Another linked worktree"):
        command_stop()
    with pytest.raises(CommandError, match="Another linked worktree"):
        command_abort(quiet=True)

    monkeypatch.chdir(temp_git_repo)
    command_abort(quiet=True)


def test_stale_foreign_owner_is_reclaimed(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """Ownership becomes reclaimable only after its local marker disappears."""
    worktree = tmp_path / "linked"
    _add_linked_worktree(temp_git_repo, worktree)
    (temp_git_repo / "README.md").write_text("# Test\nmain change\n")
    command_start(quiet=True)
    owner_path = get_active_session_owner_file_path()

    # Simulate explicit/manual cleanup of the owning worktree's local marker.
    marker_path = json.loads(owner_path.read_text())["marker_path"]
    monkeypatch.chdir(worktree)
    Path(marker_path).unlink()

    require_no_foreign_session_owner()

    assert not owner_path.exists()
