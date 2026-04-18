"""Tests for new batch command."""

from git_stage_batch.batch import read_batch_metadata
from git_stage_batch.batch.state_refs import get_batch_content_ref_name

import subprocess

import pytest

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


class TestCommandNewBatch:
    """Tests for new batch command."""

    def test_new_batch_creates_batch(self, temp_git_repo, capsys):
        """Test creating a new batch."""
        command_new_batch("test-batch")

        # Verify authoritative batch ref exists
        result = subprocess.run(
            ["git", "show-ref", get_batch_content_ref_name("test-batch")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        # Verify success message
        captured = capsys.readouterr()
        assert "Created batch 'test-batch'" in captured.err

    def test_new_batch_with_note(self, temp_git_repo, capsys):
        """Test creating a batch with a note."""
        command_new_batch("test-batch", note="Test description")

        # Verify authoritative batch ref exists
        result = subprocess.run(
            ["git", "show-ref", get_batch_content_ref_name("test-batch")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        # Verify note is stored in metadata
        metadata = read_batch_metadata("test-batch")
        assert metadata["note"] == "Test description"

        # Verify success message
        captured = capsys.readouterr()
        assert "Created batch 'test-batch'" in captured.err

    def test_new_batch_outside_repo_raises_error(self, tmp_path, monkeypatch):
        """Test that creating a batch outside a repo raises an error."""
        # Change to a non-repo directory
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_new_batch("test-batch")

    def test_new_batch_duplicate_raises_error(self, temp_git_repo):
        """Test that creating a duplicate batch raises an error."""
        # Create first batch
        command_new_batch("test-batch")

        # Attempt to create duplicate
        with pytest.raises(CommandError):
            command_new_batch("test-batch")
