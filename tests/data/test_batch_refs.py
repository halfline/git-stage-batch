"""Tests for batch reference snapshot and restore."""

import json
import subprocess

import pytest

from git_stage_batch.data.batch_refs import restore_batch_refs, snapshot_batch_refs
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.git import run_git_command
from git_stage_batch.utils.paths import (
    get_batch_directory_path,
    get_batch_metadata_file_path,
    get_batch_refs_snapshot_file_path,
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

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestSnapshotBatchRefs:
    """Tests for snapshot_batch_refs function."""

    def test_snapshot_with_no_batches(self, temp_git_repo):
        """Test snapshotting when no batches exist."""
        snapshot_batch_refs()

        snapshot_path = get_batch_refs_snapshot_file_path()
        assert snapshot_path.exists()

        snapshot_data = json.loads(read_text_file_contents(snapshot_path))
        assert snapshot_data == {}

    def test_snapshot_with_batches(self, temp_git_repo):
        """Test snapshotting existing batches."""
        # Create a batch manually
        batch_name = "test-batch"
        result = run_git_command(["rev-parse", "HEAD"])
        commit_sha = result.stdout.strip()

        # Create batch ref
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

        # Create metadata
        metadata_path = get_batch_metadata_file_path(batch_name)
        metadata = {"note": "Test note", "created_at": "2026-03-13T12:00:00Z"}
        from git_stage_batch.utils.file_io import write_text_file_contents
        write_text_file_contents(metadata_path, json.dumps(metadata))

        # Snapshot
        snapshot_batch_refs()

        # Verify snapshot
        snapshot_path = get_batch_refs_snapshot_file_path()
        snapshot_data = json.loads(read_text_file_contents(snapshot_path))

        assert batch_name in snapshot_data
        assert snapshot_data[batch_name]["commit_sha"] == commit_sha
        assert snapshot_data[batch_name]["note"] == "Test note"
        assert snapshot_data[batch_name]["created_at"] == "2026-03-13T12:00:00Z"


class TestRestoreBatchRefs:
    """Tests for restore_batch_refs function."""

    def test_restore_with_no_snapshot(self, temp_git_repo):
        """Test restoring when no snapshot exists."""
        # Should not error
        restore_batch_refs()

    def test_restore_drops_new_batches(self, temp_git_repo):
        """Test that batches created after snapshot are dropped."""
        # Create empty snapshot
        snapshot_batch_refs()

        # Create a new batch
        batch_name = "new-batch"
        result = run_git_command(["rev-parse", "HEAD"])
        commit_sha = result.stdout.strip()
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

        # Verify batch exists
        result = run_git_command(["show-ref", f"refs/batches/{batch_name}"], check=False)
        assert result.returncode == 0

        # Restore
        restore_batch_refs()

        # Verify batch was dropped
        result = run_git_command(["show-ref", f"refs/batches/{batch_name}"], check=False)
        assert result.returncode != 0

    def test_restore_recreates_deleted_batches(self, temp_git_repo):
        """Test that deleted batches are restored."""
        # Create a batch
        batch_name = "test-batch"
        result = run_git_command(["rev-parse", "HEAD"])
        commit_sha = result.stdout.strip()
        run_git_command(["update-ref", f"refs/batches/{batch_name}", commit_sha])

        # Create metadata
        metadata_path = get_batch_metadata_file_path(batch_name)
        metadata = {"note": "Test note", "created_at": "2026-03-13T12:00:00Z"}
        from git_stage_batch.utils.file_io import write_text_file_contents
        write_text_file_contents(metadata_path, json.dumps(metadata))

        # Snapshot
        snapshot_batch_refs()

        # Delete the batch
        run_git_command(["update-ref", "-d", f"refs/batches/{batch_name}"])

        # Verify batch is gone
        result = run_git_command(["show-ref", f"refs/batches/{batch_name}"], check=False)
        assert result.returncode != 0

        # Restore
        restore_batch_refs()

        # Verify batch was restored
        result = run_git_command(["show-ref", f"refs/batches/{batch_name}"], check=False)
        assert result.returncode == 0

        # Verify metadata was restored
        assert metadata_path.exists()
        restored_metadata = json.loads(read_text_file_contents(metadata_path))
        assert restored_metadata["note"] == "Test note"

    def test_restore_reverts_modified_batches(self, temp_git_repo):
        """Test that modified batch refs are reverted."""
        # Create a batch
        batch_name = "test-batch"
        result = run_git_command(["rev-parse", "HEAD"])
        original_sha = result.stdout.strip()
        run_git_command(["update-ref", f"refs/batches/{batch_name}", original_sha])

        # Snapshot
        snapshot_batch_refs()

        # Make another commit
        (temp_git_repo / "newfile.txt").write_text("content\n")
        subprocess.run(["git", "add", "newfile.txt"], check=True)
        subprocess.run(["git", "commit", "-m", "New commit"], check=True, capture_output=True)

        # Update batch to point to new commit
        result = run_git_command(["rev-parse", "HEAD"])
        new_sha = result.stdout.strip()
        run_git_command(["update-ref", f"refs/batches/{batch_name}", new_sha])

        # Verify batch points to new commit
        result = run_git_command(["rev-parse", f"refs/batches/{batch_name}"])
        assert result.stdout.strip() == new_sha

        # Restore
        restore_batch_refs()

        # Verify batch was reverted to original commit
        result = run_git_command(["rev-parse", f"refs/batches/{batch_name}"])
        assert result.stdout.strip() == original_sha
