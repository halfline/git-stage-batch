"""Tests for include command."""

import subprocess

import pytest

from git_stage_batch.commands.include import command_include, command_include_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
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


class TestCommandInclude:
    """Tests for include command."""

    def test_include_stages_hunk(self, temp_git_repo, capsys):
        """Test that include stages a hunk."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_include()

        # Check that changes are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk staged" in captured.err

    def test_include_no_changes(self, temp_git_repo, capsys):
        """Test include when no changes exist."""
        command_include()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_include_multiple_hunks(self, temp_git_repo, capsys):
        """Test including multiple hunks sequentially."""
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

        # Include first hunk
        command_include()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.err

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.err

        # Verify both are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" in result.stdout

    def test_include_all_hunks_processed(self, temp_git_repo, capsys):
        """Test include when all hunks have been processed."""
        # Modify README before starting
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_start()

        # Include the only hunk
        command_include()
        capsys.readouterr()  # Clear output

        # Try to include again - should say no more hunks
        command_include()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err


class TestCommandIncludeLine:
    """Tests for command_include_line."""

    def test_include_line_requires_current_hunk(self, temp_git_repo):
        """Test that include --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_include_line("1")

    def test_include_line_stages_single_addition(self, temp_git_repo):
        """Test including a single added line."""
        # Create a file with content
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file - add new lines
        test_file.write_text("line1\nnew line\nline2\n")

        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)  # Load the hunk

        # Include only the added line (ID 1)
        command_include_line("1")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new line" in result.stdout
        assert result.stdout == "line1\nnew line\nline2\n"

    def test_include_line_stages_multiple_lines(self, temp_git_repo, capsys):
        """Test including multiple lines."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add multiple lines
        test_file.write_text("line1\nline2\nline3\nline4\n")

        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Include lines 1 and 3
        command_include_line("1,3")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "line2" in result.stdout
        assert "line4" in result.stdout
        assert result.stdout == "line1\nline2\nline4\n"

        captured = capsys.readouterr()
        assert "Included line(s): 1,3" in captured.err

    def test_include_line_with_range(self, temp_git_repo):
        """Test including a range of lines."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("a\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add many lines
        test_file.write_text("a\nb\nc\nd\ne\nf\n")

        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Include lines 1-3
        command_include_line("1-3")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "a\nb\nc\nd\n"

    def test_include_line_incremental(self, temp_git_repo):
        """Test including lines incrementally."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify file - replace with multiple lines
        test_file.write_text("line1\nline2\nline3\n")

        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Include line 2 (which adds line1)
        command_include_line("2")

        # Include line 3 (which adds line2) after recalculation
        command_include_line("2")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        # Both lines should be staged
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_include_line_handles_deletions(self, temp_git_repo):
        """Test that include --line handles deletions correctly."""
        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep1\nremove\nkeep2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Remove a line
        test_file.write_text("keep1\nkeep2\n")

        command_start()
        find_and_cache_next_unblocked_hunk(quiet=True)

        # Include the deletion (line 1)
        command_include_line("1")

        # Check staged content - line should be removed
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "remove" not in result.stdout
        assert result.stdout == "keep1\nkeep2\n"
