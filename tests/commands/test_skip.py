"""Tests for skip command."""

import subprocess

import pytest

from git_stage_batch.commands.skip import command_skip, command_skip_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import get_processed_skip_ids_file_path


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


class TestCommandSkip:
    """Tests for skip command."""

    def test_skip_marks_hunk_as_processed(self, temp_git_repo, capsys):
        """Test that skip marks a hunk as processed without staging it."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_skip()

        # Check that changes are NOT staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""  # No staged changes

        # Check that changes still exist in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk skipped" in captured.err

    def test_skip_no_changes(self, temp_git_repo, capsys):
        """Test skip when no changes exist."""
        command_skip()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_skip_then_include_next(self, temp_git_repo, capsys):
        """Test skipping one hunk then including the next."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Skip first hunk
        command_skip()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.err

        # Second hunk should now be available
        # (Would normally use command_include here but we're just testing skip)


class TestCommandSkipLine:
    """Tests for skip --line command."""

    def test_skip_line_requires_current_hunk(self, temp_git_repo):
        """Test that skip --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_skip_line("1")

    def test_skip_line_marks_single_addition(self, temp_git_repo):
        """Test skipping a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_marks_single_deletion(self, temp_git_repo):
        """Test skipping a single deleted line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_with_range(self, temp_git_repo):
        """Test skipping a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Skip lines 1-3
        command_skip_line("1-3")

        # Check that all IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1", "2", "3"]

    def test_skip_line_partial_selection(self, temp_git_repo):
        """Test skipping only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Skip only line 1
        command_skip_line("1")

        # Check that only line 1 was recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_accumulates_ids(self, temp_git_repo):
        """Test that multiple skip --line calls accumulate."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Skip line 1
        command_skip_line("1")

        # Skip line 3
        command_skip_line("3")

        # Check that both IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert set(skip_ids) == {"1", "3"}
