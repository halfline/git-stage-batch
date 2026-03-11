"""Tests for state management and filesystem utilities."""

import subprocess

import pytest

from git_stage_batch.state import (
    append_lines_to_file,
    exit_with_error,
    get_git_repository_root_path,
    read_text_file_contents,
    require_git_repository,
    run_git_command,
    write_text_file_contents,
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


class TestErrorHandling:
    """Tests for error handling utilities."""

    def test_exit_with_error(self, capsys):
        """Test exit_with_error prints message and exits."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_error("Test error message")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Test error message" in captured.err

    def test_exit_with_error_custom_code(self, capsys):
        """Test exit_with_error with custom exit code."""
        with pytest.raises(SystemExit) as exc_info:
            exit_with_error("Custom error", exit_code=42)

        assert exc_info.value.code == 42


class TestGitUtilities:
    """Tests for git command utilities."""

    def test_run_git_command_success(self, temp_git_repo):
        """Test running a successful git command."""
        result = run_git_command(["status", "--short"])
        assert result.returncode == 0
        assert isinstance(result.stdout, str)

    def test_run_git_command_failure(self, temp_git_repo):
        """Test running a failing git command."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-command"])

    def test_run_git_command_no_check(self, temp_git_repo):
        """Test running a command without checking return code."""
        result = run_git_command(["invalid-command"], check=False)
        assert result.returncode != 0

    def test_require_git_repository_success(self, temp_git_repo):
        """Test require_git_repository in a valid repo."""
        require_git_repository()  # Should not raise

    def test_require_git_repository_failure(self, tmp_path, monkeypatch):
        """Test require_git_repository outside a git repo."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            require_git_repository()
        assert exc_info.value.code == 128  # Git's exit code for "not a git repository"

    def test_get_git_repository_root_path(self, temp_git_repo):
        """Test getting the repository root path."""
        # Create a subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        import os
        os.chdir(subdir)

        root = get_git_repository_root_path()
        assert root == temp_git_repo


class TestFileUtilities:
    """Tests for file I/O utilities."""

    def test_read_text_file_contents_existing(self, tmp_path):
        """Test reading an existing file."""
        file_path = tmp_path / "test.txt"
        file_path.write_text("Hello, world!", encoding="utf-8")

        content = read_text_file_contents(file_path)
        assert content == "Hello, world!"

    def test_read_text_file_contents_nonexistent(self, tmp_path):
        """Test reading a nonexistent file returns empty string."""
        file_path = tmp_path / "nonexistent.txt"
        content = read_text_file_contents(file_path)
        assert content == ""

    def test_write_text_file_contents(self, tmp_path):
        """Test writing file contents."""
        file_path = tmp_path / "output.txt"
        write_text_file_contents(file_path, "Test content\n")

        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == "Test content\n"

    def test_write_text_file_contents_creates_parent(self, tmp_path):
        """Test writing file contents creates parent directories."""
        file_path = tmp_path / "nested" / "dir" / "file.txt"
        write_text_file_contents(file_path, "Nested content\n")

        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == "Nested content\n"

    def test_append_lines_to_file(self, tmp_path):
        """Test appending lines to a file."""
        file_path = tmp_path / "append.txt"

        append_lines_to_file(file_path, ["line1", "line2", "line3"])
        content = file_path.read_text(encoding="utf-8")
        assert content == "line1\nline2\nline3\n"

        # Append more lines
        append_lines_to_file(file_path, ["line4"])
        content = file_path.read_text(encoding="utf-8")
        assert content == "line1\nline2\nline3\nline4\n"

    def test_append_lines_strips_trailing_whitespace(self, tmp_path):
        """Test that append_lines_to_file strips trailing whitespace."""
        file_path = tmp_path / "append.txt"
        append_lines_to_file(file_path, ["line1  \t", "line2\n\n"])
        content = file_path.read_text(encoding="utf-8")
        assert content == "line1\nline2\n"
