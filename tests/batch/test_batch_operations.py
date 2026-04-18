"""Tests for batch operations."""

import json
import subprocess

import pytest

from git_stage_batch.batch.operations import create_batch, delete_batch, update_batch_note
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.state_refs import get_batch_content_ref_name, get_batch_state_ref_name
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import run_git_command
from git_stage_batch.utils.paths import get_batch_metadata_file_path


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


def test_create_batch_creates_state_metadata(temp_git_repo):
    """Test that create_batch creates Git-backed metadata."""
    create_batch("test-batch", "Test note")

    metadata = read_batch_metadata("test-batch")
    assert metadata["note"] == "Test note"
    assert "created_at" in metadata
    assert not get_batch_metadata_file_path("test-batch").exists()


def test_create_batch_creates_ref(temp_git_repo):
    """Test that create_batch creates git ref."""
    create_batch("test-batch", "Test note")

    # Verify ref exists
    result = run_git_command(["show-ref", "--verify", get_batch_content_ref_name("test-batch")], check=False)
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
    result = run_git_command(["show-ref", "--verify", get_batch_content_ref_name("test-batch")], check=False)
    assert result.returncode == 0

    delete_batch("test-batch")

    # Verify ref is gone
    for refname in (get_batch_content_ref_name("test-batch"), get_batch_state_ref_name("test-batch")):
        result = run_git_command(["show-ref", "--verify", refname], check=False)
        assert result.returncode != 0


def test_delete_batch_removes_metadata(temp_git_repo):
    """Test that delete_batch removes metadata directory."""
    create_batch("test-batch", "Test")

    metadata_path = get_batch_metadata_file_path("test-batch")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({"note": "legacy"}))

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
