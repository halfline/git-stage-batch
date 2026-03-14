"""Tests for batch command handlers."""

import subprocess

import pytest

from git_stage_batch.state import (
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


def test_new_command_creates_batch(temp_git_repo):
    """Test new command creates a batch."""
    result = subprocess.run(
        ["git-stage-batch", "new", "test-batch", "--note", "Test note"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Created batch 'test-batch'" in result.stdout

    # Verify batch ref exists
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert ref_result.returncode == 0


def test_new_command_without_note(temp_git_repo):
    """Test creating batch without note."""
    result = subprocess.run(
        ["git-stage-batch", "new", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0


def test_new_command_duplicate_fails(temp_git_repo):
    """Test creating duplicate batch fails."""
    subprocess.run(["git-stage-batch", "new", "test-batch"], capture_output=True)

    result = subprocess.run(
        ["git-stage-batch", "new", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr


def test_list_command_empty(temp_git_repo):
    """Test list command with no batches."""
    result = subprocess.run(
        ["git-stage-batch", "list"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "No batches exist" in result.stdout


def test_list_command_shows_batches(temp_git_repo):
    """Test list command shows created batches."""
    subprocess.run(["git-stage-batch", "new", "batch-a", "--note", "First"], capture_output=True)
    subprocess.run(["git-stage-batch", "new", "batch-b", "--note", "Second"], capture_output=True)

    result = subprocess.run(
        ["git-stage-batch", "list"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "batch-a" in result.stdout
    assert "First" in result.stdout
    assert "batch-b" in result.stdout
    assert "Second" in result.stdout


def test_drop_command_deletes_batch(temp_git_repo):
    """Test drop command deletes batch."""
    subprocess.run(["git-stage-batch", "new", "test-batch"], capture_output=True)

    # Verify it exists
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert ref_result.returncode == 0

    result = subprocess.run(
        ["git-stage-batch", "drop", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Dropped batch 'test-batch'" in result.stdout

    # Verify it's gone
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert ref_result.returncode != 0


def test_drop_command_nonexistent_fails(temp_git_repo):
    """Test dropping nonexistent batch fails."""
    result = subprocess.run(
        ["git-stage-batch", "drop", "nonexistent"],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "does not exist" in result.stderr


def test_annotate_command_updates_note(temp_git_repo):
    """Test annotate command updates batch note."""
    subprocess.run(["git-stage-batch", "new", "test-batch", "--note", "Original"], capture_output=True)

    result = subprocess.run(
        ["git-stage-batch", "annotate", "test-batch", "Updated note"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Updated note for batch 'test-batch'" in result.stdout

    # Verify note was updated
    list_result = subprocess.run(
        ["git-stage-batch", "list"],
        capture_output=True,
        text=True
    )
    assert "Updated note" in list_result.stdout
    assert "Original" not in list_result.stdout


def test_annotate_command_nonexistent_fails(temp_git_repo):
    """Test annotating nonexistent batch fails."""
    result = subprocess.run(
        ["git-stage-batch", "annotate", "nonexistent", "Note"],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "does not exist" in result.stderr


def test_batch_name_validation_slash(temp_git_repo):
    """Test that invalid batch name is rejected."""
    result = subprocess.run(
        ["git-stage-batch", "new", "invalid/name"],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "cannot contain" in result.stderr or "Batch name" in result.stderr
