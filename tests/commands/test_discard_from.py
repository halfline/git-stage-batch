"""Tests for discard from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.include import command_include_to_batch

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.data.hunk_tracking import render_batch_file_display
from git_stage_batch.commands.discard_from import command_discard_from_batch
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


class TestCommandDiscardFromBatch:
    """Tests for discard from batch command."""

    def test_discard_from_batch_removes_changes(self, temp_git_repo, capsys):
        """Test discarding changes from a batch removes them from working tree."""

        # Commit a file first
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes and save to batch
        (temp_git_repo / "file.txt").write_text("batch version\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # File still has batch changes in working tree

        command_discard_from_batch("test-batch")

        # File should be back to committed state
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

        captured = capsys.readouterr()
        assert "Discarded changes from batch" in captured.err

    def test_discard_from_batch_partial_atomic_unit_shows_required_lines(self, temp_git_repo):
        """Partial replacement selections should keep the atomic-selection guidance."""
        test_file = temp_git_repo / "file.txt"
        test_file.write_text("old value\nkeep\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("new value\nkeep\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        rendered = render_batch_file_display("test-batch", "file.txt")
        new_value_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text == "new value"
        )

        with pytest.raises(CommandError, match="must be selected together") as exc_info:
            command_discard_from_batch("test-batch", line_ids=str(new_value_gutter), file="file.txt")

        assert "Use: --line" in exc_info.value.message

    def test_discard_from_empty_batch_fails(self, temp_git_repo):
        """Test discarding from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

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
