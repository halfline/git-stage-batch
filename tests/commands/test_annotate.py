"""Tests for annotate batch command."""

import json
import subprocess

import pytest

from git_stage_batch.commands.annotate import command_annotate_batch
from git_stage_batch.commands.new import command_new_batch
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import get_batch_metadata_file_path


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
        # Create a batch without a note
        command_new_batch("test-batch")

        # Add a note
        command_annotate_batch("test-batch", "This is a note")

        # Verify note is stored
        metadata_path = get_batch_metadata_file_path("test-batch")
        assert metadata_path.exists()
        metadata = json.loads(read_text_file_contents(metadata_path))
        assert metadata["note"] == "This is a note"

        # Verify success message
        captured = capsys.readouterr()
        assert "Updated note for batch 'test-batch'" in captured.err

    def test_annotate_batch_updates_note(self, temp_git_repo, capsys):
        """Test updating an existing note."""
        # Create a batch with a note
        command_new_batch("test-batch", note="Original note")

        # Update the note
        command_annotate_batch("test-batch", "Updated note")

        # Verify note is updated
        metadata_path = get_batch_metadata_file_path("test-batch")
        metadata = json.loads(read_text_file_contents(metadata_path))
        assert metadata["note"] == "Updated note"

        # Verify success message
        captured = capsys.readouterr()
        assert "Updated note for batch 'test-batch'" in captured.err

    def test_annotate_nonexistent_batch_raises_error(self, temp_git_repo):
        """Test annotating a nonexistent batch raises an error."""
        with pytest.raises(CommandError):
            command_annotate_batch("nonexistent-batch", "Some note")

    def test_annotate_batch_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Test annotating a batch outside a repo raises an error."""
        # Change to a non-repo directory
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_annotate_batch("test-batch", "Some note")

    def test_annotate_batch_with_empty_note(self, temp_git_repo):
        """Test annotating a batch with an empty note."""
        command_new_batch("test-batch", note="Original note")

        # Update with empty note
        command_annotate_batch("test-batch", "")

        # Verify note is cleared
        metadata_path = get_batch_metadata_file_path("test-batch")
        metadata = json.loads(read_text_file_contents(metadata_path))
        assert metadata["note"] == ""
