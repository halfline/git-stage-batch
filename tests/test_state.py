"""Tests for state management and filesystem utilities."""

import subprocess

import pytest

from git_stage_batch.state import (
    CommandError,
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
    get_current_hunk_hash_file_path,
    get_current_hunk_patch_file_path,
    get_git_repository_root_path,
    get_state_directory_path,
    read_file_paths_file,
    read_text_file_contents,
    remove_file_path_from_file,
    require_git_repository,
    run_git_command,
    stream_git_command,
    write_file_paths_file,
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

    def test_exit_with_error(self):
        """Test exit_with_error raises CommandError with message."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("Test error message")

        assert exc_info.value.message == "Test error message"
        assert exc_info.value.exit_code == 1

    def test_exit_with_error_custom_code(self):
        """Test exit_with_error with custom exit code."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("Custom error", exit_code=42)

        assert exc_info.value.message == "Custom error"
        assert exc_info.value.exit_code == 42


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
        with pytest.raises(CommandError) as exc_info:
            require_git_repository()
        assert exc_info.value.exit_code == 128

    def test_get_git_repository_root_path(self, temp_git_repo):
        """Test getting the repository root path."""
        # Create a subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        import os
        os.chdir(subdir)

        root = get_git_repository_root_path()
        assert root == temp_git_repo

    def test_stream_git_command_success(self, temp_git_repo):
        """Test streaming git command output line by line."""
        lines = list(stream_git_command(["status", "--short"]))
        assert isinstance(lines, list)
        # Should complete without error

    def test_stream_git_command_early_termination(self, temp_git_repo):
        """Test stopping early doesn't cause pipe deadlock or errors.

        This is a regression test for a bug where breaking out of the
        iteration early would cause the git subprocess to block on write()
        when its stdout pipe filled up, leading to a deadlock when the
        generator's cleanup code called wait().
        """
        # Create a file with many lines to ensure git diff produces
        # enough output to fill the pipe buffer (64KB on Linux)
        large_file = temp_git_repo / "large.txt"
        large_file.write_text("\n".join([f"Line {i}" for i in range(10000)]))
        subprocess.run(["git", "add", "large.txt"], check=True, cwd=temp_git_repo)
        subprocess.run(["git", "commit", "-m", "Add large file"], check=True, cwd=temp_git_repo)

        # Make changes to generate a large diff
        large_file.write_text("\n".join([f"Modified line {i}" for i in range(10000)]))

        # Read only the first few lines then stop
        line_count = 0
        for line in stream_git_command(["diff", "HEAD"]):
            line_count += 1
            if line_count >= 10:
                break

        assert line_count == 10
        # If we got here without hanging, the test passed

    def test_stream_git_command_failure(self, temp_git_repo):
        """Test streaming git command that fails raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            # Consume all output to trigger error check in finally block
            list(stream_git_command(["invalid-command"]))

    def test_stream_git_command_early_termination_no_error_on_cancelled(self, temp_git_repo):
        """Test early termination doesn't raise error even if git was killed."""
        # Create large output
        large_file = temp_git_repo / "large2.txt"
        large_file.write_text("\n".join([f"Line {i}" for i in range(10000)]))
        subprocess.run(["git", "add", "large2.txt"], check=True, cwd=temp_git_repo)
        subprocess.run(["git", "commit", "-m", "Add large file 2"], check=True, cwd=temp_git_repo)
        large_file.write_text("\n".join([f"Modified line {i}" for i in range(10000)]))

        # Should not raise even though we terminate git process early
        gen = stream_git_command(["diff", "HEAD"])
        next(gen)  # Read one line
        gen.close()  # Explicitly close, triggering GeneratorExit
        # No exception should be raised


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
        path = get_state_directory_path() / "test-file-list"

        write_file_paths_file(path, ["file1.txt", "file2.txt", "file3.txt"])
        remove_file_path_from_file(path, "file2.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file3.txt"]

    def test_remove_file_path_from_file_nonexistent(self, temp_git_repo):
        """Test removing a nonexistent file path doesn't error."""
        ensure_state_directory_exists()
        path = get_state_directory_path() / "test-file-list"

        write_file_paths_file(path, ["file1.txt", "file2.txt"])
        remove_file_path_from_file(path, "nonexistent.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]
