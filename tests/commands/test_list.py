"""Tests for list batches command."""

import subprocess

import pytest

from git_stage_batch.commands.list import command_list_batches
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


class TestCommandListBatches:
    """Tests for list batches command."""

    def test_list_batches_empty(self, temp_git_repo, capsys):
        """Test listing batches when none exist."""
        command_list_batches()

        captured = capsys.readouterr()
        assert "No batches found" in captured.out

    def test_list_batches_single(self, temp_git_repo, capsys):
        """Test listing a single batch."""
        command_new_batch("test-batch")

        command_list_batches()

        captured = capsys.readouterr()
        assert "test-batch" in captured.out
        assert "No batches found" not in captured.out

    def test_list_batches_multiple(self, temp_git_repo, capsys):
        """Test listing multiple batches."""
        command_new_batch("batch-a")
        command_new_batch("batch-b")
        command_new_batch("batch-c")

        command_list_batches()

        captured = capsys.readouterr()
        assert "batch-a" in captured.out
        assert "batch-b" in captured.out
        assert "batch-c" in captured.out

    def test_list_batches_sorted(self, temp_git_repo, capsys):
        """Test that batches are listed in sorted order."""
        command_new_batch("z-batch")
        command_new_batch("a-batch")
        command_new_batch("m-batch")

        # Clear output from batch creation
        capsys.readouterr()

        command_list_batches()

        captured = capsys.readouterr()
        lines = [line for line in captured.out.splitlines() if line]
        assert lines == ["a-batch", "m-batch", "z-batch"]

    def test_list_batches_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Test that listing batches outside a repo raises an error."""
        # Change to a non-repo directory
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_list_batches()
