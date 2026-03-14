"""Tests for core batch metadata operations."""

import json
import subprocess

import pytest

from git_stage_batch.batch import (
    create_batch,
    delete_batch,
    get_batch_commit_sha,
    list_batch_names,
    read_batch_metadata,
    update_batch_note,
)
from git_stage_batch.state import (
    CommandError,
    get_batch_metadata_file_path,
    read_text_file_contents,
    run_git_command,
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


def test_create_batch_creates_metadata(temp_git_repo):
    """Test that create_batch creates metadata file."""
    create_batch("test-batch", "Test note")

    metadata_path = get_batch_metadata_file_path("test-batch")
    assert metadata_path.exists()

    metadata = json.loads(read_text_file_contents(metadata_path))
    assert metadata["note"] == "Test note"
    assert "created_at" in metadata


def test_create_batch_creates_ref(temp_git_repo):
    """Test that create_batch creates git ref."""
    create_batch("test-batch", "Test note")

    # Verify ref exists
    result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert result.returncode == 0


def test_create_batch_without_note(temp_git_repo):
    """Test creating batch without note."""
    create_batch("test-batch")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == ""
    assert "created_at" in metadata


def test_create_batch_duplicate_fails(temp_git_repo):
    """Test that creating duplicate batch fails."""
    create_batch("test-batch", "First")

    with pytest.raises(CommandError):
        create_batch("test-batch", "Duplicate")


def test_delete_batch_removes_ref(temp_git_repo):
    """Test that delete_batch removes git ref."""
    create_batch("test-batch", "Test")

    # Verify ref exists
    result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert result.returncode == 0

    delete_batch("test-batch")

    # Verify ref is gone
    result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert result.returncode != 0


def test_delete_batch_removes_metadata(temp_git_repo):
    """Test that delete_batch removes metadata directory."""
    create_batch("test-batch", "Test")

    metadata_path = get_batch_metadata_file_path("test-batch")
    assert metadata_path.exists()

    delete_batch("test-batch")

    assert not metadata_path.exists()
    assert not metadata_path.parent.exists()


def test_delete_nonexistent_batch_fails(temp_git_repo):
    """Test that deleting nonexistent batch fails."""
    with pytest.raises(CommandError):
        delete_batch("nonexistent")


def test_update_batch_note(temp_git_repo):
    """Test updating batch note."""
    create_batch("test-batch", "Original note")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == "Original note"
    original_created_at = metadata["created_at"]

    update_batch_note("test-batch", "Updated note")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == "Updated note"
    assert metadata["created_at"] == original_created_at  # created_at unchanged


def test_update_note_nonexistent_batch_fails(temp_git_repo):
    """Test that updating note on nonexistent batch fails."""
    with pytest.raises(CommandError):
        update_batch_note("nonexistent", "Note")


def test_read_batch_metadata(temp_git_repo):
    """Test reading batch metadata."""
    create_batch("test-batch", "Test note")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == "Test note"
    assert "created_at" in metadata
    assert len(metadata) == 2  # Only note and created_at


def test_read_metadata_nonexistent_batch(temp_git_repo):
    """Test reading metadata for nonexistent batch."""
    metadata = read_batch_metadata("nonexistent")
    assert metadata["note"] == ""
    assert metadata["created_at"] == ""


def test_get_batch_commit_sha(temp_git_repo):
    """Test getting batch commit SHA."""
    create_batch("test-batch", "Test")

    commit_sha = get_batch_commit_sha("test-batch")
    assert commit_sha is not None
    assert len(commit_sha) == 40  # Full SHA


def test_get_commit_sha_nonexistent_batch(temp_git_repo):
    """Test getting commit SHA for nonexistent batch."""
    commit_sha = get_batch_commit_sha("nonexistent")
    assert commit_sha is None


def test_list_batch_names_empty(temp_git_repo):
    """Test listing batch names when none exist."""
    batches = list_batch_names()
    assert batches == []


def test_list_batch_names(temp_git_repo):
    """Test listing batch names."""
    create_batch("batch-a", "First")
    create_batch("batch-c", "Third")
    create_batch("batch-b", "Second")

    batches = list_batch_names()
    assert batches == ["batch-a", "batch-b", "batch-c"]  # Sorted


def test_batch_name_validation_slash(temp_git_repo):
    """Test that batch name with slash fails."""
    with pytest.raises(CommandError):
        create_batch("invalid/name", "Test")


def test_batch_name_validation_empty(temp_git_repo):
    """Test that empty batch name fails."""
    with pytest.raises(CommandError):
        create_batch("", "Test")


def test_batch_name_validation_leading_dot(temp_git_repo):
    """Test that batch name starting with dot fails."""
    with pytest.raises(CommandError):
        create_batch(".hidden", "Test")
