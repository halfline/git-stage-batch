"""Tests for show from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.commands.show_from import command_show_from_batch
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


class TestCommandShowFromBatch:
    """Tests for show from batch command."""

    def test_show_from_batch_displays_changes(self, temp_git_repo, capsys):
        """Test showing changes from a batch."""
        create_batch("test-batch")
        add_file_to_batch("test-batch", "file.txt", "content\n")

        command_show_from_batch("test-batch")

        captured = capsys.readouterr()
        assert "file.txt" in captured.out
        assert "content" in captured.out
        assert "[#1]" in captured.out  # Check for line ID annotation

    def test_show_from_empty_batch_fails(self, temp_git_repo):
        """Test showing from an empty batch fails."""
        create_batch("empty-batch")
        # Add baseline file to batch so there's no diff
        add_file_to_batch("empty-batch", "README.md", "# Test\n")

        with pytest.raises(CommandError):
            command_show_from_batch("empty-batch")

    def test_show_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test showing from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_show_from_batch("nonexistent")

    def test_show_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test showing from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_show_from_batch("test-batch")
