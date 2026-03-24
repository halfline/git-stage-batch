"""Tests for discard from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.exceptions import CommandError


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


class TestCommandDiscardFromBatch:
    """Tests for discard from batch command."""

    def test_discard_from_batch_removes_changes(self, temp_git_repo, capsys):
        """Test discarding changes from a batch removes them from working tree."""
        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with modified file
        create_batch("test-batch")
        add_file_to_batch("test-batch", "README.md", "# Test\n")  # Baseline
        add_file_to_batch("test-batch", "file.txt", "batch version\n")

        # Apply batch changes to working tree
        (temp_git_repo / "file.txt").write_text("batch version\n")

        command_discard_from_batch("test-batch")

        # File should be back to committed state
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

        captured = capsys.readouterr()
        assert "Discarded changes from batch" in captured.err

    def test_discard_from_empty_batch_fails(self, temp_git_repo):
        """Test discarding from an empty batch fails."""
        create_batch("empty-batch")
        # Add baseline file to batch so there's no diff
        add_file_to_batch("empty-batch", "README.md", "# Test\n")

        with pytest.raises(CommandError):
            command_discard_from_batch("empty-batch")

    def test_discard_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test discarding from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_discard_from_batch("nonexistent")

    def test_discard_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test discarding from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_discard_from_batch("test-batch")
