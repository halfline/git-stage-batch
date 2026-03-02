"""Tests for state management and filesystem utilities."""

import subprocess
import sys
from pathlib import Path

import pytest

from git_stage_batch.state import (
    append_lines_to_file,
    clear_current_hunk_state_files,
    ensure_state_directory_exists,
    exit_with_error,
    get_block_list_file_path,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_git_repository_root_path,
    get_index_snapshot_file_path,
    get_processed_exclude_ids_file_path,
    get_processed_include_ids_file_path,
    get_state_directory_path,
    get_working_tree_snapshot_file_path,
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
        assert exc_info.value.code == 1

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


class TestStatePaths:
    """Tests for state path functions."""

    def test_get_state_directory_path(self, temp_git_repo):
        """Test getting the state directory path."""
        state_dir = get_state_directory_path()
        assert state_dir == temp_git_repo / ".git" / "git-stage-batch"

    def test_get_block_list_file_path(self, temp_git_repo):
        """Test getting the blocklist file path."""
        path = get_block_list_file_path()
        assert path.name == "blocklist"
        assert path.parent.name == "git-stage-batch"

    def test_get_current_hunk_patch_file_path(self, temp_git_repo):
        """Test getting the current hunk patch file path."""
        path = get_current_hunk_patch_file_path()
        assert path.name == "current-hunk.patch"

    def test_get_current_hunk_hash_file_path(self, temp_git_repo):
        """Test getting the current hunk hash file path."""
        path = get_current_hunk_hash_file_path()
        assert path.name == "current.hash"

    def test_get_current_lines_json_file_path(self, temp_git_repo):
        """Test getting the current lines JSON file path."""
        path = get_current_lines_json_file_path()
        assert path.name == "current-lines.json"

    def test_get_processed_include_ids_file_path(self, temp_git_repo):
        """Test getting the processed include IDs file path."""
        path = get_processed_include_ids_file_path()
        assert path.name == "processed.include"

    def test_get_processed_exclude_ids_file_path(self, temp_git_repo):
        """Test getting the processed exclude IDs file path."""
        path = get_processed_exclude_ids_file_path()
        assert path.name == "processed.exclude"

    def test_get_index_snapshot_file_path(self, temp_git_repo):
        """Test getting the index snapshot file path."""
        path = get_index_snapshot_file_path()
        assert path.name == "snapshot-base"

    def test_get_working_tree_snapshot_file_path(self, temp_git_repo):
        """Test getting the working tree snapshot file path."""
        path = get_working_tree_snapshot_file_path()
        assert path.name == "snapshot-new"


class TestStateManagement:
    """Tests for state management functions."""

    def test_ensure_state_directory_exists(self, temp_git_repo):
        """Test creating the state directory."""
        ensure_state_directory_exists()

        state_dir = get_state_directory_path()
        assert state_dir.exists()
        assert state_dir.is_dir()

        blocklist = get_block_list_file_path()
        assert blocklist.exists()
        assert blocklist.is_file()

    def test_ensure_state_directory_exists_idempotent(self, temp_git_repo):
        """Test that ensure_state_directory_exists is idempotent."""
        ensure_state_directory_exists()
        ensure_state_directory_exists()  # Should not raise

        assert get_state_directory_path().exists()

    def test_clear_current_hunk_state_files(self, temp_git_repo):
        """Test clearing current hunk state files."""
        ensure_state_directory_exists()

        # Create some state files
        state_files = [
            get_current_hunk_patch_file_path(),
            get_current_hunk_hash_file_path(),
            get_current_lines_json_file_path(),
            get_processed_include_ids_file_path(),
            get_processed_exclude_ids_file_path(),
            get_index_snapshot_file_path(),
            get_working_tree_snapshot_file_path(),
        ]

        for path in state_files:
            write_text_file_contents(path, "test content")
            assert path.exists()

        # Clear them
        clear_current_hunk_state_files()

        # Verify they're gone
        for path in state_files:
            assert not path.exists()

    def test_clear_current_hunk_state_files_when_empty(self, temp_git_repo):
        """Test clearing state files when none exist."""
        ensure_state_directory_exists()
        clear_current_hunk_state_files()  # Should not raise
