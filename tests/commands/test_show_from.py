"""Tests for show from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import ensure_state_directory_exists


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

    # Initialize session for batch operations
    ensure_state_directory_exists()
    initialize_abort_state()

    return repo


class TestCommandShowFromBatch:
    """Tests for show from batch command."""

    def test_show_from_batch_displays_changes(self, temp_git_repo, capsys):
        """Test showing changes from a batch."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.include import command_include_to_batch

        # Create a new file and save to batch
        (temp_git_repo / "file.txt").write_text("content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        command_show_from_batch("test-batch")

        captured = capsys.readouterr()
        assert "file.txt" in captured.out
        assert "content" in captured.out
        assert "[#1]" in captured.out  # Check for line ID annotation

    def test_show_from_empty_batch_succeeds(self, temp_git_repo):
        """Test showing from an empty batch succeeds with no output."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        # Empty batch should succeed but produce no output
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
