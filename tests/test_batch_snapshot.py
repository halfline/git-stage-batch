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


def test_snapshot_batch_refs_with_batches(temp_git_repo):
    """Test snapshot captures batch refs and metadata."""
    # Create batch refs
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/batch1", head_sha])
    run_git_command(["update-ref", "refs/batches/batch2", head_sha])

    # Create metadata for batches
    metadata1 = {"note": "First batch", "created_at": "2024-01-01T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("batch1"),
        json.dumps(metadata1)
    )

    metadata2 = {"note": "Second batch", "created_at": "2024-01-02T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("batch2"),
        json.dumps(metadata2)
    )

    # Snapshot
    snapshot_batch_refs()

    # Verify snapshot
    snapshot_path = get_batch_refs_snapshot_file_path()
    data = json.loads(read_text_file_contents(snapshot_path))

    assert "batch1" in data
    assert "batch2" in data
    assert data["batch1"]["commit_sha"] == head_sha
    assert data["batch1"]["note"] == "First batch"
    assert data["batch1"]["created_at"] == "2024-01-01T00:00:00Z"
    assert data["batch2"]["commit_sha"] == head_sha
    assert data["batch2"]["note"] == "Second batch"


def test_restore_batch_refs_empty(temp_git_repo):
    """Test restore with empty snapshot does nothing."""
    snapshot_batch_refs()  # Empty snapshot
    restore_batch_refs()  # Should not error


def test_restore_batch_refs_drops_new_batches(temp_git_repo):
    """Test restore drops batches created after snapshot."""
    # Create snapshot with no batches
    snapshot_batch_refs()

    # Create a batch after snapshot
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/new-batch", head_sha])

    # Verify batch exists
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode == 0

    # Restore
    restore_batch_refs()

    # Verify batch is dropped
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode != 0


def test_restore_batch_refs_restores_dropped_batches(temp_git_repo):
    """Test restore recreates batches deleted after snapshot."""
    # Create batch
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/old-batch", head_sha])

    metadata = {"note": "Old batch", "created_at": "2024-01-01T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("old-batch"),
        json.dumps(metadata)
    )

    # Snapshot
    snapshot_batch_refs()

    # Delete batch
    run_git_command(["update-ref", "-d", "refs/batches/old-batch"])

    # Verify batch is gone
    result = run_git_command(["show-ref", "--verify", "refs/batches/old-batch"], check=False)
    assert result.returncode != 0

    # Restore
    restore_batch_refs()

    # Verify batch is restored
    result = run_git_command(["show-ref", "--verify", "refs/batches/old-batch"], check=False)
    assert result.returncode == 0

    restored_sha = run_git_command(["rev-parse", "refs/batches/old-batch"]).stdout.strip()
    assert restored_sha == head_sha

    # Verify metadata is restored
    metadata_path = get_batch_metadata_file_path("old-batch")
    assert metadata_path.exists()
    restored_metadata = json.loads(read_text_file_contents(metadata_path))
    assert restored_metadata["note"] == "Old batch"


def test_restore_batch_refs_reverts_mutations(temp_git_repo):
    """Test restore reverts batch ref mutations to snapshot state."""
    # Create batch at initial HEAD
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/test-batch", head_sha])

    metadata = {"note": "Test batch", "created_at": "2024-01-01T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("test-batch"),
        json.dumps(metadata)
    )

    # Snapshot
    snapshot_batch_refs()

    # Create a new commit
    (temp_git_repo / "file.txt").write_text("new content\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "New commit"], check=True, capture_output=True)
    new_head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    # Mutate batch ref to point to new commit
    run_git_command(["update-ref", "refs/batches/test-batch", new_head_sha])

    # Verify mutation
    current_sha = run_git_command(["rev-parse", "refs/batches/test-batch"]).stdout.strip()
    assert current_sha == new_head_sha
    assert current_sha != head_sha

    # Restore
    restore_batch_refs()

    # Verify ref is reverted to snapshot state
    restored_sha = run_git_command(["rev-parse", "refs/batches/test-batch"]).stdout.strip()
    assert restored_sha == head_sha


def test_restore_batch_refs_handles_mixed_operations(temp_git_repo):
    """Test restore handles created, dropped, and mutated batches together."""
    # Setup: Create one batch
    head_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/existing", head_sha])

    metadata_existing = {"note": "Existing", "created_at": "2024-01-01T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("existing"),
        json.dumps(metadata_existing)
    )

    run_git_command(["update-ref", "refs/batches/to-drop", head_sha])
    metadata_drop = {"note": "Will drop", "created_at": "2024-01-02T00:00:00Z"}
    write_text_file_contents(
        get_batch_metadata_file_path("to-drop"),
        json.dumps(metadata_drop)
    )

    # Snapshot
    snapshot_batch_refs()

    # Create new commit for mutations
    (temp_git_repo / "file.txt").write_text("new\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "New"], check=True, capture_output=True)
    new_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()

    # Operations: create new, drop old, mutate existing
    run_git_command(["update-ref", "refs/batches/new-batch", new_sha])
    run_git_command(["update-ref", "-d", "refs/batches/to-drop"])
    run_git_command(["update-ref", "refs/batches/existing", new_sha])

    # Restore
    restore_batch_refs()

    # Verify: new-batch dropped
    result = run_git_command(["show-ref", "--verify", "refs/batches/new-batch"], check=False)
    assert result.returncode != 0

    # Verify: to-drop restored
    result = run_git_command(["show-ref", "--verify", "refs/batches/to-drop"], check=False)
    assert result.returncode == 0
    restored_sha = run_git_command(["rev-parse", "refs/batches/to-drop"]).stdout.strip()
    assert restored_sha == head_sha

    # Verify: existing reverted
    restored_sha = run_git_command(["rev-parse", "refs/batches/existing"]).stdout.strip()
    assert restored_sha == head_sha
