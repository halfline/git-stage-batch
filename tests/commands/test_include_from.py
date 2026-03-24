"""Tests for include from batch command."""

import subprocess

import pytest

from git_stage_batch.batch import add_file_to_batch, create_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import run_git_command


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


class TestCommandIncludeFromBatch:
    """Tests for include from batch command."""

    def test_include_from_batch_stages_changes(self, temp_git_repo, capsys):
        """Test including changes from a batch stages them."""
        create_batch("test-batch")
        add_file_to_batch("test-batch", "README.md", "# Test\n")  # Baseline
        add_file_to_batch("test-batch", "new.txt", "new content\n")

        command_include_from_batch("test-batch")

        # Verify file is staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert "new.txt" in result.stdout

        captured = capsys.readouterr()
        assert "Staged changes from batch" in captured.err

    def test_include_from_empty_batch_fails(self, temp_git_repo):
        """Test including from an empty batch fails."""
        create_batch("empty-batch")
        # Add baseline file to batch so there's no diff
        add_file_to_batch("empty-batch", "README.md", "# Test\n")

        with pytest.raises(CommandError):
            command_include_from_batch("empty-batch")

    def test_include_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test including from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_include_from_batch("nonexistent")

    def test_include_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test including from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_include_from_batch("test-batch")

    def test_include_from_batch_file_mode_filters_to_current_file(self, temp_git_repo, capsys):
        """Test that --file mode filters batch diff to current file only."""
        from unittest.mock import patch, MagicMock
        from git_stage_batch.core.models import CurrentLines, LineEntry

        # Create baseline with multiple files
        (temp_git_repo / "file1.txt").write_text("file1 content\n")
        (temp_git_repo / "file2.txt").write_text("file2 content\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch with changes to both files
        create_batch("test-batch")
        add_file_to_batch("test-batch", "file1.txt", "file1 MODIFIED\n")
        add_file_to_batch("test-batch", "file2.txt", "file2 MODIFIED\n")

        # Mock cached hunk state to make it look like file1.txt is the current file
        mock_lines = CurrentLines(
            path="file1.txt",
            header=MagicMock(),  # Mock header (not relevant for this test)
            lines=[LineEntry(id=1, kind="+", text="file1 MODIFIED\n", old_line_number=None, new_line_number=1)]
        )

        with patch("git_stage_batch.data.hunk_tracking.require_current_hunk_and_check_stale"):
            with patch("git_stage_batch.data.line_state.load_current_lines_from_state", return_value=mock_lines):
                # Use --file mode to stage from batch (should only stage file1)
                command_include_from_batch("test-batch", file_only=True)

        # Verify only file1 is staged (not file2)
        result = run_git_command(["diff", "--cached", "--name-only"])
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        assert "file1.txt" in staged_files
        assert "file2.txt" not in staged_files

        # Verify file1 has the batch content
        result = run_git_command(["show", ":file1.txt"])
        assert result.stdout == "file1 MODIFIED\n"

        captured = capsys.readouterr()
        assert "Staged changes for file1.txt from batch" in captured.err

    def test_include_from_batch_file_mode_requires_cached_hunk(self, temp_git_repo):
        """Test that --file mode requires a cached hunk."""
        create_batch("test-batch")
        add_file_to_batch("test-batch", "new.txt", "content\n")

        # Try to use --file without starting session
        with pytest.raises(CommandError):
            command_include_from_batch("test-batch", file_only=True)
