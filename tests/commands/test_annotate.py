"""Tests for annotate batch command."""

import subprocess

import pytest

from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.commands.annotate import command_annotate_batch
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


class TestCommandAnnotateBatch:
    """Tests for annotate batch command."""

    def test_annotate_batch_adds_note(self, temp_git_repo, capsys):
        """Test adding a note to a batch."""
        command_new_batch("test-batch")

        command_annotate_batch("test-batch", "This is a note")

        metadata = read_batch_metadata("test-batch")
        assert metadata["note"] == "This is a note"

        captured = capsys.readouterr()
        assert "Updated note for batch 'test-batch'" in captured.err

    def test_annotate_batch_updates_note(self, temp_git_repo, capsys):
        """Test updating an existing note."""
        command_new_batch("test-batch", note="Original note")

        command_annotate_batch("test-batch", "Updated note")

        metadata = read_batch_metadata("test-batch")
        assert metadata["note"] == "Updated note"

        captured = capsys.readouterr()
        assert "Updated note for batch 'test-batch'" in captured.err

    def test_annotate_nonexistent_batch_raises_error(self, temp_git_repo):
        """Test annotating a nonexistent batch raises an error."""
        with pytest.raises(CommandError):
            command_annotate_batch("nonexistent-batch", "Some note")

    def test_annotate_batch_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Test annotating a batch outside a repo raises an error."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_annotate_batch("test-batch", "Some note")

    def test_annotate_batch_with_empty_note(self, temp_git_repo):
        """Test annotating a batch with an empty note."""
        command_new_batch("test-batch", note="Original note")

        command_annotate_batch("test-batch", "")

        metadata = read_batch_metadata("test-batch")
        assert metadata["note"] == ""
