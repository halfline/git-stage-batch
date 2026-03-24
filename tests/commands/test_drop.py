"""Tests for drop batch command."""

import subprocess

import pytest

from git_stage_batch.commands.drop import command_drop_batch
from git_stage_batch.commands.new import command_new_batch
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


class TestCommandDropBatch:
    """Tests for drop batch command."""

    def test_drop_batch_removes_ref(self, temp_git_repo, capsys):
        """Test that dropping a batch removes its ref."""
        # Create a batch
        command_new_batch("test-batch")

        # Verify batch exists
        result = subprocess.run(
            ["git", "show-ref", "refs/batches/test-batch"],
            capture_output=True,
        )
        assert result.returncode == 0

        # Drop the batch
        command_drop_batch("test-batch")

        # Verify batch ref is gone
        result = subprocess.run(
            ["git", "show-ref", "refs/batches/test-batch"],
            capture_output=True,
        )
        assert result.returncode != 0

        # Verify success message
        captured = capsys.readouterr()
        assert "Deleted batch 'test-batch'" in captured.err

    def test_drop_nonexistent_batch_raises_error(self, temp_git_repo):
        """Test that dropping a nonexistent batch raises an error."""
        with pytest.raises(CommandError):
            command_drop_batch("nonexistent-batch")

    def test_drop_batch_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Test that dropping a batch outside a repo raises an error."""
        # Change to a non-repo directory
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_drop_batch("test-batch")

    def test_drop_batch_multiple_batches(self, temp_git_repo):
        """Test dropping one batch leaves others intact."""
        # Create multiple batches
        command_new_batch("batch-a")
        command_new_batch("batch-b")
        command_new_batch("batch-c")

        # Drop one batch
        command_drop_batch("batch-b")

        # Verify batch-b is gone
        result = subprocess.run(
            ["git", "show-ref", "refs/batches/batch-b"],
            capture_output=True,
        )
        assert result.returncode != 0

        # Verify other batches still exist
        result = subprocess.run(
            ["git", "show-ref", "refs/batches/batch-a"],
            capture_output=True,
        )
        assert result.returncode == 0

        result = subprocess.run(
            ["git", "show-ref", "refs/batches/batch-c"],
            capture_output=True,
        )
        assert result.returncode == 0
