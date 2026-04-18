"""Tests for git command execution utilities."""

from git_stage_batch.utils.git import stream_git_command
from git_stage_batch.utils.git import resolve_file_path_to_repo_relative
from git_stage_batch.utils.git import read_gitignore_lines
from git_stage_batch.utils.git import get_gitignore_path
from git_stage_batch.utils.git import write_gitignore_lines
from git_stage_batch.utils.git import add_file_to_gitignore
from git_stage_batch.utils.git import remove_file_from_gitignore

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import (
    get_git_repository_root_path,
    require_git_repository,
    run_git_command,
)


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


class TestRunGitCommand:
    """Tests for run_git_command function."""

    def test_successful_command_returns_result(self, temp_git_repo):
        """Test that successful git command returns CompletedProcess."""
        result = run_git_command(["status", "--short"])

        assert result.returncode == 0
        assert isinstance(result.stdout, str)

    def test_failed_command_with_check_raises(self, temp_git_repo):
        """Test that failed command with check=True raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-command"])

    def test_failed_command_without_check_returns_result(self, temp_git_repo):
        """Test that failed command with check=False returns result."""
        result = run_git_command(["invalid-command"], check=False)

        assert result.returncode != 0

    def test_text_output_returns_strings(self, temp_git_repo):
        """Test that text_output=True returns string output."""
        result = run_git_command(["status"], text_output=True)

        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)

    def test_captures_stdout(self, temp_git_repo):
        """Test that stdout is captured."""
        result = run_git_command(["rev-parse", "--git-dir"])

        assert ".git" in result.stdout


class TestStreamGitCommand:
    """Tests for stream_git_command function."""

    def test_stream_git_command_success(self, temp_git_repo):
        """Test streaming a successful git command."""

        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Stream the diff
        lines = list(stream_git_command(["diff", "--cached"]))
        assert len(lines) > 0
        # Should have lines from the diff
        assert any(b"line 1" in line for line in lines)

    def test_stream_git_command_early_termination(self, temp_git_repo):
        """Test that stream can be terminated early without error."""

        # Create a large file
        large_content = "\n".join([f"line {i}" for i in range(1000)])
        test_file = temp_git_repo / "large.txt"
        test_file.write_text(large_content)
        subprocess.run(["git", "add", "large.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Take only first few lines
        stream = stream_git_command(["diff", "--cached"])
        first_lines = []
        for i, line in enumerate(stream):
            first_lines.append(line)
            if i >= 5:
                break

        assert len(first_lines) == 6

    def test_stream_git_command_failure(self, temp_git_repo):
        """Test that streaming a failing command raises error."""

        with pytest.raises(subprocess.CalledProcessError):
            # Consume the entire stream to trigger error check
            list(stream_git_command(["invalid-command"]))


class TestRequireGitRepository:
    """Tests for require_git_repository function."""

    def test_succeeds_in_git_repository(self, temp_git_repo):
        """Test that function succeeds when inside a git repository."""
        # Should not raise
        require_git_repository()

    def test_exits_outside_git_repository(self, tmp_path, monkeypatch):
        """Test that function exits with error outside git repository."""
        # Change to non-git directory
        monkeypatch.chdir(tmp_path)

        with pytest.raises(CommandError):
            require_git_repository()


class TestGetGitRepositoryRootPath:
    """Tests for get_git_repository_root_path function."""

    def test_returns_repository_root(self, temp_git_repo):
        """Test that function returns the repository root path."""
        root = get_git_repository_root_path()

        assert isinstance(root, Path)
        assert root.is_absolute()
        assert (root / ".git").exists()

    def test_returns_same_path_from_subdirectory(self, temp_git_repo, monkeypatch):
        """Test that function returns root even from subdirectory."""
        # Create subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        root = get_git_repository_root_path()

        assert root == temp_git_repo


class TestResolveFilePathToRepoRelative:
    """Tests for resolve_file_path_to_repo_relative function."""

    def test_resolve_file_path_to_repo_relative_relative(self, temp_git_repo):
        """Test that relative paths are returned as-is."""

        result = resolve_file_path_to_repo_relative("src/file.py")
        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_absolute(self, temp_git_repo):
        """Test that absolute paths inside repo are made relative."""

        absolute_path = str(temp_git_repo / "src" / "file.py")
        result = resolve_file_path_to_repo_relative(absolute_path)
        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_outside_repo(self, temp_git_repo, tmp_path):
        """Test that paths outside repo are returned as-is."""

        outside_path = str(tmp_path / "outside.txt")
        result = resolve_file_path_to_repo_relative(outside_path)
        assert result == outside_path


class TestGitignoreManipulation:
    """Tests for .gitignore manipulation functions."""

    def test_read_gitignore_lines_nonexistent(self, temp_git_repo):
        """Test reading .gitignore when it doesn't exist."""

        lines = read_gitignore_lines()
        assert lines == []

    def test_read_gitignore_lines_existing(self, temp_git_repo):
        """Test reading existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n.env\n")

        lines = read_gitignore_lines()
        assert lines == ["*.pyc\n", "__pycache__/\n", ".env\n"]

    def test_write_gitignore_lines(self, temp_git_repo):
        """Test writing .gitignore lines."""

        lines = ["*.pyc\n", "__pycache__/\n", ".env\n"]
        write_gitignore_lines(lines)

        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert content == "*.pyc\n__pycache__/\n.env\n"

    def test_add_file_to_gitignore_new(self, temp_git_repo):
        """Test adding a file to .gitignore when .gitignore doesn't exist."""

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_existing(self, temp_git_repo):
        """Test adding a file to existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n")

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "__pycache__/\n" in lines
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_no_duplicates(self, temp_git_repo):
        """Test adding a file twice doesn't create duplicates."""

        add_file_to_gitignore("test.txt")
        add_file_to_gitignore("test.txt")

        content = get_gitignore_path().read_text()
        # Should only appear once
        assert content.count("test.txt") == 1

    def test_add_file_to_gitignore_preserves_no_trailing_newline(self, temp_git_repo):
        """Test adding to .gitignore when existing file has no trailing newline."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc")  # No trailing newline

        add_file_to_gitignore("test.txt")

        content = gitignore.read_text()
        assert content == "*.pyc\ntest.txt\n"

    def test_remove_file_from_gitignore_with_marker(self, temp_git_repo):
        """Test removing a file from .gitignore."""

        add_file_to_gitignore("test.txt")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines

    def test_remove_file_from_gitignore_without_marker(self, temp_git_repo):
        """Test that we can remove any entry from .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("test.txt\n*.pyc\n")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        # Entry should be removed
        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines
        # Other entries should remain
        assert "*.pyc\n" in lines

    def test_remove_file_from_gitignore_preserves_other_entries(self, temp_git_repo):
        """Test that removing one entry preserves others."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        add_file_to_gitignore("test1.txt")
        add_file_to_gitignore("test2.txt")

        remove_file_from_gitignore("test1.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "test1.txt\n" not in lines
        assert "test2.txt\n" in lines

    def test_remove_file_from_gitignore_nonexistent(self, temp_git_repo):
        """Test removing a file that doesn't exist in .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        removed = remove_file_from_gitignore("nonexistent.txt")
        assert removed is False

        # Original content unchanged
        assert gitignore.read_text() == "*.pyc\n"
