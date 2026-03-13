"""Tests for state management and filesystem utilities."""

import subprocess

import pytest

from git_stage_batch.state import (
    add_file_to_gitignore,
    append_file_path_to_file,
    append_lines_to_file,
    ensure_state_directory_exists,
    exit_with_error,
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
    get_auto_added_files_file_path,
    get_block_list_file_path,
    get_context_lines,
    get_context_lines_file_path,
    get_blocked_files_file_path,
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_git_repository_root_path,
    get_gitignore_path,
    get_index_snapshot_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    get_state_directory_path,
    get_working_tree_snapshot_file_path,
    read_file_paths_file,
    read_gitignore_lines,
    read_text_file_contents,
    remove_file_from_gitignore,
    remove_file_path_from_file,
    require_git_repository,
    resolve_file_path_to_repo_relative,
    run_git_command,
    write_file_paths_file,
    write_gitignore_lines,
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


class TestStateDirectory:
    """Tests for state directory management."""

    def test_get_state_directory_path(self, temp_git_repo):
        """Test getting the state directory path."""
        state_dir = get_state_directory_path()
        assert state_dir == temp_git_repo / ".git" / "git-stage-batch"

    def test_ensure_state_directory_exists_creates_directory(self, temp_git_repo):
        """Test that ensure_state_directory_exists creates the directory."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        ensure_state_directory_exists()

        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_ensure_state_directory_exists_idempotent(self, temp_git_repo):
        """Test that ensure_state_directory_exists is idempotent."""
        ensure_state_directory_exists()
        ensure_state_directory_exists()  # Should not raise

        state_dir = get_state_directory_path()
        assert state_dir.exists()

    def test_get_context_lines_file_path(self, temp_git_repo):
        """Test getting the context lines file path."""
        context_path = get_context_lines_file_path()
        state_dir = get_state_directory_path()
        assert context_path == state_dir / "context-lines"

    def test_get_block_list_file_path(self, temp_git_repo):
        """Test getting the blocklist file path."""
        blocklist_path = get_block_list_file_path()
        state_dir = get_state_directory_path()
        assert blocklist_path == state_dir / "blocklist"

    def test_get_current_hunk_patch_file_path(self, temp_git_repo):
        """Test getting the current hunk patch file path."""
        patch_path = get_current_hunk_patch_file_path()
        state_dir = get_state_directory_path()
        assert patch_path == state_dir / "current-hunk-patch"

    def test_get_current_hunk_hash_file_path(self, temp_git_repo):
        """Test getting the current hunk hash file path."""
        hash_path = get_current_hunk_hash_file_path()
        state_dir = get_state_directory_path()
        assert hash_path == state_dir / "current-hunk-hash"

    def test_get_abort_head_file_path(self, temp_git_repo):
        """Test getting the abort head file path."""
        abort_head_path = get_abort_head_file_path()
        state_dir = get_state_directory_path()
        assert abort_head_path == state_dir / "abort-head"

    def test_get_abort_stash_file_path(self, temp_git_repo):
        """Test getting the abort stash file path."""
        abort_stash_path = get_abort_stash_file_path()
        state_dir = get_state_directory_path()
        assert abort_stash_path == state_dir / "abort-stash"

    def test_get_abort_snapshots_directory_path(self, temp_git_repo):
        """Test getting the abort snapshots directory path."""
        snapshots_dir = get_abort_snapshots_directory_path()
        state_dir = get_state_directory_path()
        assert snapshots_dir == state_dir / "snapshots"

    def test_get_abort_snapshot_list_file_path(self, temp_git_repo):
        """Test getting the abort snapshot list file path."""
        snapshot_list_path = get_abort_snapshot_list_file_path()
        state_dir = get_state_directory_path()
        assert snapshot_list_path == state_dir / "snapshot-list"

    def test_get_auto_added_files_file_path(self, temp_git_repo):
        """Test getting the auto-added files file path."""
        auto_added_path = get_auto_added_files_file_path()
        state_dir = get_state_directory_path()
        assert auto_added_path == state_dir / "auto-added-files"

    def test_get_blocked_files_file_path(self, temp_git_repo):
        """Test getting the blocked files file path."""
        blocked_path = get_blocked_files_file_path()
        state_dir = get_state_directory_path()
        assert blocked_path == state_dir / "blocked-files"

    def test_get_current_lines_json_file_path(self, temp_git_repo):
        """Test getting the current lines JSON file path."""
        lines_json_path = get_current_lines_json_file_path()
        state_dir = get_state_directory_path()
        assert lines_json_path == state_dir / "current-lines.json"

    def test_get_gitignore_path(self, temp_git_repo):
        """Test getting the .gitignore path."""
        gitignore_path = get_gitignore_path()
        assert gitignore_path == temp_git_repo / ".gitignore"

    def test_get_index_snapshot_file_path(self, temp_git_repo):
        """Test getting the index snapshot file path."""
        index_snapshot_path = get_index_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert index_snapshot_path == state_dir / "index-snapshot"

    def test_get_working_tree_snapshot_file_path(self, temp_git_repo):
        """Test getting the working tree snapshot file path."""
        working_tree_snapshot_path = get_working_tree_snapshot_file_path()
        state_dir = get_state_directory_path()
        assert working_tree_snapshot_path == state_dir / "working-tree-snapshot"

    def test_get_processed_include_ids_file_path(self, temp_git_repo):
        """Test getting the processed include IDs file path."""
        include_ids_path = get_processed_include_ids_file_path()
        state_dir = get_state_directory_path()
        assert include_ids_path == state_dir / "processed.include"

    def test_get_processed_skip_ids_file_path(self, temp_git_repo):
        """Test getting the processed skip IDs file path."""
        skip_ids_path = get_processed_skip_ids_file_path()
        state_dir = get_state_directory_path()
        assert skip_ids_path == state_dir / "processed.skip"


class TestContextLines:
    """Tests for context lines state management."""

    def test_get_context_lines_default(self, temp_git_repo):
        """Test that get_context_lines defaults to 3 when file doesn't exist."""
        ensure_state_directory_exists()
        assert get_context_lines() == 3

    def test_get_context_lines_reads_value(self, temp_git_repo):
        """Test that get_context_lines reads the stored value."""
        ensure_state_directory_exists()
        context_file = get_context_lines_file_path()
        write_text_file_contents(context_file, "5")

        assert get_context_lines() == 5

    def test_get_context_lines_invalid_value(self, temp_git_repo):
        """Test that get_context_lines defaults to 3 on invalid value."""
        ensure_state_directory_exists()
        context_file = get_context_lines_file_path()
        write_text_file_contents(context_file, "not-a-number")

        assert get_context_lines() == 3


class TestFilePathListManagement:
    """Tests for file path list management functions."""

    def test_read_file_paths_file_empty(self, temp_git_repo):
        """Test reading an empty file paths file."""
        ensure_state_directory_exists()
        path = get_auto_added_files_file_path()
        result = read_file_paths_file(path)
        assert result == []

    def test_read_file_paths_file_nonexistent(self, tmp_path):
        """Test reading a nonexistent file paths file."""
        path = tmp_path / "nonexistent.txt"
        result = read_file_paths_file(path)
        assert result == []

    def test_write_file_paths_file(self, temp_git_repo):
        """Test writing file paths to a file."""
        ensure_state_directory_exists()
        path = get_auto_added_files_file_path()

        file_paths = ["path/to/file1.txt", "path/to/file2.txt", "another/file.py"]
        write_file_paths_file(path, file_paths)

        # Read back and verify sorted and deduplicated
        result = read_file_paths_file(path)
        assert result == sorted(file_paths)

    def test_write_file_paths_file_deduplicates(self, temp_git_repo):
        """Test that write_file_paths_file deduplicates entries."""
        ensure_state_directory_exists()
        path = get_auto_added_files_file_path()

        file_paths = ["file1.txt", "file2.txt", "file1.txt", "file3.txt", "file2.txt"]
        write_file_paths_file(path, file_paths)

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_file_path_to_file(self, temp_git_repo):
        """Test appending a file path to a list."""
        ensure_state_directory_exists()
        path = get_auto_added_files_file_path()

        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file3.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_file_path_to_file_no_duplicates(self, temp_git_repo):
        """Test that appending doesn't create duplicates."""
        ensure_state_directory_exists()
        path = get_auto_added_files_file_path()

        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file1.txt")  # Duplicate

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]

    def test_remove_file_path_from_file(self, temp_git_repo):
        """Test removing a file path from a list."""
        ensure_state_directory_exists()
        path = get_blocked_files_file_path()

        write_file_paths_file(path, ["file1.txt", "file2.txt", "file3.txt"])
        remove_file_path_from_file(path, "file2.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file3.txt"]

    def test_remove_file_path_from_file_nonexistent(self, temp_git_repo):
        """Test removing a nonexistent file path doesn't error."""
        ensure_state_directory_exists()
        path = get_blocked_files_file_path()

        write_file_paths_file(path, ["file1.txt", "file2.txt"])
        remove_file_path_from_file(path, "nonexistent.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]

    def test_resolve_file_path_to_repo_relative_relative(self, temp_git_repo):
        """Test resolving a relative file path."""
        result = resolve_file_path_to_repo_relative("src/file.txt")
        assert result == "src/file.txt"

    def test_resolve_file_path_to_repo_relative_absolute(self, temp_git_repo):
        """Test resolving an absolute file path inside repo."""
        abs_path = temp_git_repo / "src" / "file.txt"
        result = resolve_file_path_to_repo_relative(str(abs_path))
        assert result == "src/file.txt"

    def test_resolve_file_path_to_repo_relative_outside_repo(self, temp_git_repo):
        """Test resolving a path outside the repo."""
        outside_path = "/tmp/some/file.txt"
        result = resolve_file_path_to_repo_relative(outside_path)
        assert result == outside_path  # Returns as-is


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
