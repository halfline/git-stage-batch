"""Tests for include from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.include import command_include_to_batch
from unittest.mock import patch, MagicMock
from git_stage_batch.core.models import LineLevelChange, LineEntry

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.data.hunk_tracking import render_batch_file_display
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import run_git_command
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


class TestCommandIncludeFromBatch:
    """Tests for include from batch command."""

    def test_include_from_batch_stages_changes(self, temp_git_repo, capsys):
        """Test including changes from a batch stages them."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("new content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Remove from working tree and unstage
        (temp_git_repo / "new.txt").unlink()
        run_git_command(["reset", "HEAD", "new.txt"], check=False)

        command_include_from_batch("test-batch")

        # Verify file is staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert "new.txt" in result.stdout
        assert (temp_git_repo / "new.txt").read_text() == "new content\n"

        captured = capsys.readouterr()
        assert "Staged changes from batch" in captured.err

    def test_include_from_empty_batch_fails(self, temp_git_repo):
        """Test including from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

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

    def test_include_from_batch_file_mode_filters_to_selected_file(self, temp_git_repo, capsys):
        """Test that --file mode filters batch diff to selected file only."""

        # Create baseline with multiple files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new lines to both files and save to batch
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\nfile1 added\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\nfile2 added\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset files to original
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")

        # Mock cached hunk state to make it look like file1.txt is the selected file
        mock_lines = LineLevelChange(
            path="file1.txt",
            header=MagicMock(),  # Mock header (not relevant for this test)
            lines=[LineEntry(id=1, kind="+", text_bytes=b"file1 added\n", text="file1 added\n", old_line_number=None, new_line_number=3)]
        )

        with patch("git_stage_batch.data.hunk_tracking.require_selected_hunk"):
            with patch("git_stage_batch.data.line_state.load_line_changes_from_state", return_value=mock_lines):
                # Use --file mode to stage from batch (should only stage file1)
                command_include_from_batch("test-batch", file="")

        # Verify only file1 is staged (not file2)
        result = run_git_command(["diff", "--cached", "--name-only"])
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        assert "file1.txt" in staged_files
        assert "file2.txt" not in staged_files

        # Verify file1 has the added line
        result = run_git_command(["show", ":file1.txt"])
        assert "file1 added" in result.stdout
        assert (temp_git_repo / "file1.txt").read_text() == "line 1\nline 2\nfile1 added\n"
        assert (temp_git_repo / "file2.txt").read_text() == "line A\nline B\n"

        captured = capsys.readouterr()
        assert "Staged changes for file1.txt from batch" in captured.err

    def test_include_from_batch_file_mode_requires_cached_hunk(self, temp_git_repo):
        """Test that --file mode requires a cached hunk."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Try to use --file without cached hunk (no fetch_next_change call after command_start)
        with pytest.raises(CommandError):
            command_include_from_batch("test-batch", file="")

    def test_include_from_batch_as_replaces_presence_only_selection(self, temp_git_repo):
        """Test include --from --line --as replaces claimed batch content before staging."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        batch_value_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text == "batch value"
        )

        command_include_from_batch(
            "test-batch",
            line_ids=str(batch_value_gutter),
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_replaces_atomic_replacement_unit(self, temp_git_repo):
        """Test include --from --line --as preserves batch deletion semantics."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\nold value\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\nold value\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        replacement_gutters = sorted(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text in {"old value", "batch value"}
        )

        command_include_from_batch(
            "test-batch",
            line_ids=f"{replacement_gutters[0]}-{replacement_gutters[-1]}",
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_rejects_partial_replacement_unit(self, temp_git_repo):
        """Test include --from --line --as honors explicit replacement atomicity."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("old one\nold two\nkeep\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("new one\nnew two\nkeep\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("old one\nold two\nkeep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        new_one_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text == "new one"
        )

        with pytest.raises(CommandError, match="must be selected together"):
            command_include_from_batch(
                "test-batch",
                line_ids=str(new_one_gutter),
                file="test.txt",
                replacement_text="edited value",
            )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "old one\nold two\nkeep\n"
        assert test_file.read_text() == "old one\nold two\nkeep\n"
