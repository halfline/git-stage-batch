"""Tests for apply from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
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


class TestCommandApplyFromBatch:
    """Tests for apply from batch command."""

    def test_apply_from_batch_modifies_working_tree(self, temp_git_repo):
        """Test applying changes from a batch to working tree."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.include import command_include_to_batch

        # Commit a file with multiple lines
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add a new line and save to batch
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nnew line\nline 3\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset file to original
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")

        command_apply_from_batch("test-batch")

        # File should have the new line added
        content = (temp_git_repo / "file.txt").read_text()
        assert "new line" in content
        # Original lines should still be present
        assert "line 1" in content
        assert "line 2" in content
        assert "line 3" in content

    def test_apply_from_batch_does_not_stage(self, temp_git_repo):
        """Test that apply does not stage changes to index."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.commands.include import command_include_to_batch

        # Commit a file with multiple lines
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add a new line and save to batch
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nnew line\nline 3\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset file to original
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")

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
        # Empty batch (only contains baseline from HEAD) has no diff

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
