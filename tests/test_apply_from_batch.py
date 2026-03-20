"""Tests for apply --from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.commands import command_apply_from_batch
from git_stage_batch.state import CommandError


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


def test_apply_from_batch_modifies_working_tree(temp_git_repo):
    """Test that apply --from modifies working tree."""
    test_file = temp_git_repo / "test.txt"

    # Create batch and add modified file content
    create_batch("test-batch", "Test batch")
    add_file_to_batch("test-batch", "test.txt", "modified\nline2\nline3\n")

    # Apply batch
    command_apply_from_batch("test-batch")

    # Verify working tree was modified
    assert test_file.read_text() == "modified\nline2\nline3\n"


def test_apply_from_batch_does_not_stage(temp_git_repo):
    """Test that apply --from doesn't stage changes to index."""
    test_file = temp_git_repo / "test.txt"

    # Create batch and add modified file content
    create_batch("test-batch", "Test batch")
    add_file_to_batch("test-batch", "test.txt", "modified\nline2\nline3\n")

    # Apply batch
    command_apply_from_batch("test-batch")

    # Verify index is still clean
    result = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=temp_git_repo,
        capture_output=True,
        text=True
    )
    assert result.stdout.strip() == ""


def test_apply_from_nonexistent_batch_fails(temp_git_repo):
    """Test that apply --from fails on non-existent batch."""
    with pytest.raises(CommandError) as exc_info:
        command_apply_from_batch("nonexistent")

    assert exc_info.value.exit_code != 0
