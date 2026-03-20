"""Tests for --from batch operations."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.state import run_git_command


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


def test_show_from_batch_displays_diff(temp_git_repo):
    """Test show --from displays batch diff."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "newfile.txt", "New content\n")

    result = subprocess.run(
        ["git-stage-batch", "show", "--from", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "newfile.txt" in result.stdout
    assert "+New content" in result.stdout


def test_show_from_nonexistent_batch_fails(temp_git_repo):
    """Test show --from with nonexistent batch fails."""
    result = subprocess.run(
        ["git-stage-batch", "show", "--from", "nonexistent"],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0


def test_include_from_batch_stages_changes(temp_git_repo):
    """Test include --from stages batch changes to index."""
    # First add README to batch to match baseline, then add new file
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "README", "initial\n")  # Match baseline
    add_file_to_batch("test-batch", "newfile.txt", "New content\n")

    result = subprocess.run(
        ["git-stage-batch", "include", "--from", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Staged changes from batch" in result.stdout

    # Verify new file is staged
    diff_result = run_git_command(["diff", "--cached", "--name-only"])
    assert "newfile.txt" in diff_result.stdout


def test_include_from_batch_with_conflicts_fails(temp_git_repo):
    """Test include --from fails when batch can't apply cleanly."""
    # Create batch with file
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Batch version\n")

    # Modify working tree so batch can't apply
    (temp_git_repo / "file.txt").write_text("Different version\n")
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "Conflicting change"], check=True, capture_output=True)

    result = subprocess.run(
        ["git-stage-batch", "include", "--from", "test-batch"],
        capture_output=True,
        text=True
    )

    # Should fail with helpful message
    assert result.returncode != 0
    assert "Failed to apply" in result.stderr or "show --from" in result.stderr


def test_discard_from_batch_removes_changes(temp_git_repo):
    """Test discard --from removes batch changes from working tree."""
    # Create empty baseline (remove README)
    subprocess.run(["git", "rm", "README"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Remove README"], check=True, capture_output=True)

    # Add new file to working tree
    (temp_git_repo / "file.txt").write_text("New content\n")

    # Create batch with just the new file
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "New content\n")

    result = subprocess.run(
        ["git-stage-batch", "discard", "--from", "test-batch"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Discarded changes from batch" in result.stdout
    assert "still exists" in result.stdout  # Note about batch persisting

    # Verify file was removed from working tree
    assert not (temp_git_repo / "file.txt").exists()


def test_discard_from_batch_with_incompatible_state_fails(temp_git_repo):
    """Test discard --from fails when working tree doesn't match expectations."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    # Don't create the file in working tree, so reverse can't apply
    result = subprocess.run(
        ["git-stage-batch", "discard", "--from", "test-batch"],
        capture_output=True,
        text=True
    )

    # Should fail with helpful message
    assert result.returncode != 0


def test_show_from_empty_batch(temp_git_repo):
    """Test show --from with empty batch."""
    create_batch("empty-batch", "Test")

    result = subprocess.run(
        ["git-stage-batch", "show", "--from", "empty-batch"],
        capture_output=True,
        text=True
    )

    # Empty batch shows baseline deletions
    assert result.returncode == 0


def test_include_from_preserves_batch(temp_git_repo):
    """Test that include --from does not delete the batch."""
    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    subprocess.run(["git-stage-batch", "include", "--from", "test-batch"], capture_output=True)

    # Verify batch still exists
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert ref_result.returncode == 0


def test_discard_from_preserves_batch(temp_git_repo):
    """Test that discard --from does not delete the batch."""
    (temp_git_repo / "file.txt").write_text("Content\n")

    create_batch("test-batch", "Test")
    add_file_to_batch("test-batch", "file.txt", "Content\n")

    subprocess.run(["git-stage-batch", "discard", "--from", "test-batch"], capture_output=True)

    # Verify batch still exists
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert ref_result.returncode == 0
