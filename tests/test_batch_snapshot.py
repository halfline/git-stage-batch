"""Tests for batch snapshot and restore functions."""

import json
import subprocess

import pytest

from git_stage_batch.state import (
    get_batch_metadata_file_path,
    get_batch_refs_snapshot_file_path,
    read_text_file_contents,
    restore_batch_refs,
    run_git_command,
    snapshot_batch_refs,
    write_text_file_contents,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return tmp_path


def test_snapshot_batch_refs_empty(temp_git_repo):
    """Test snapshot with no batches creates empty snapshot."""
    snapshot_batch_refs()

    snapshot_path = get_batch_refs_snapshot_file_path()
    assert snapshot_path.exists()

    data = json.loads(read_text_file_contents(snapshot_path))
    assert data == {}


def test_restore_batch_refs_empty(temp_git_repo):
    """Test restore with empty snapshot does nothing."""
    snapshot_batch_refs()  # Empty snapshot
    restore_batch_refs()  # Should not error


