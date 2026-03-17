"""Tests for batch snapshot/restore integration with start/abort commands."""

import json
import subprocess

import pytest

from git_stage_batch.commands import command_abort, command_start, command_stop
from git_stage_batch.state import (
    get_batch_refs_snapshot_file_path,
    read_text_file_contents,
    run_git_command,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit with a file
    (repo / "test.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


def test_start_creates_batch_snapshot(temp_git_repo):
    """Test that command_start creates a batch snapshot file."""
    # Create a batch before starting session
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/test-batch", head_sha])

    # Modify file to have changes
    (temp_git_repo / "test.txt").write_text("modified\nline2\nline3\n")

    # Start session
    command_start()

    # Verify snapshot was created
    snapshot_path = get_batch_refs_snapshot_file_path()
    assert snapshot_path.exists()

    # Verify snapshot contains the batch
    snapshot_data = json.loads(read_text_file_contents(snapshot_path))
    assert "test-batch" in snapshot_data
    assert snapshot_data["test-batch"]["commit_sha"] == head_sha


def test_start_creates_empty_batch_snapshot_when_no_batches(temp_git_repo):
    """Test that command_start creates an empty snapshot when no batches exist."""
    # Modify file to have changes
    (temp_git_repo / "test.txt").write_text("modified\nline2\nline3\n")

    # Start session (no batches exist)
    command_start()

    # Verify snapshot was created and is empty
    snapshot_path = get_batch_refs_snapshot_file_path()
    assert snapshot_path.exists()

    snapshot_data = json.loads(read_text_file_contents(snapshot_path))
    assert snapshot_data == {}


def test_abort_restores_batch_snapshot(temp_git_repo):
    """Test that command_abort restores batches from snapshot."""
    # Create initial batch
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/original-batch", head_sha])

    # Modify file to have changes
    (temp_git_repo / "test.txt").write_text("modified\nline2\nline3\n")

    # Start session (creates snapshot)
    command_start()

    # Create a new batch during session
    run_git_command(["update-ref", "refs/batches/new-batch", head_sha])

    # Delete the original batch during session
    run_git_command(["update-ref", "-d", "refs/batches/original-batch"])

    # Verify current state: new-batch exists, original-batch doesn't
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode == 0

    result = run_git_command(["show-ref", "--verify", "refs/batches/original-batch"], check=False)
    assert result.returncode != 0

    # Abort session
    command_abort()

    # Verify batches were restored: new-batch dropped, original-batch restored
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode != 0

    result = run_git_command(["show-ref", "--verify", "refs/batches/original-batch"], check=False)
    assert result.returncode == 0


def test_stop_does_not_restore_batches(temp_git_repo):
    """Test that command_stop preserves batch changes (doesn't restore)."""
    # Create initial batch
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/original-batch", head_sha])

    # Modify file to have changes
    (temp_git_repo / "test.txt").write_text("modified\nline2\nline3\n")

    # Start session
    command_start()

    # Create a new batch during session
    run_git_command(["update-ref", "refs/batches/new-batch", head_sha])

    # Delete the original batch during session
    run_git_command(["update-ref", "-d", "refs/batches/original-batch"])

    # Stop session (should preserve changes)
    command_stop()

    # Verify batches were NOT restored: new-batch still exists, original-batch still gone
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode == 0

    result = run_git_command(["show-ref", "--verify", "refs/batches/original-batch"], check=False)
    assert result.returncode != 0
