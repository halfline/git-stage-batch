"""Tests for apply from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
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


class TestCommandApplyFromBatch:
    """Tests for apply from batch command."""

    def test_apply_from_batch_modifies_working_tree(self, temp_git_repo):
        """Test applying changes from a batch to working tree."""
        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with modified file
        create_batch("test-batch")
        add_file_to_batch("test-batch", "README.md", "# Test\n")  # Baseline
        add_file_to_batch("test-batch", "file.txt", "batch version\n")

        command_apply_from_batch("test-batch")

        # File should have batch changes
        assert (temp_git_repo / "file.txt").read_text() == "batch version\n"

    def test_apply_from_batch_does_not_stage(self, temp_git_repo):
        """Test that apply does not stage changes to index."""
        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with modified file
        create_batch("test-batch")
        add_file_to_batch("test-batch", "README.md", "# Test\n")  # Baseline
        add_file_to_batch("test-batch", "file.txt", "batch version\n")

        command_apply_from_batch("test-batch")

        # Index should be clean (no staged changes)
        result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == ""

    def test_apply_from_empty_batch_fails(self, temp_git_repo):
        """Test applying from an empty batch fails."""
        create_batch("empty-batch")
        # Add baseline file to batch so there's no diff
        add_file_to_batch("empty-batch", "README.md", "# Test\n")

        with pytest.raises(CommandError):
            command_apply_from_batch("empty-batch")

    def test_apply_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test applying from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_apply_from_batch("nonexistent")

    def test_apply_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test applying from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_apply_from_batch("test-batch")
