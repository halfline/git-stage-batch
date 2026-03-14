"""Tests for --to batch operations (skip --to, discard --to)."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.batch import list_batch_files, read_file_from_batch
from git_stage_batch.commands import (
    command_discard_to_batch,
    command_skip_to_batch,
    command_start,
    command_stop,
)
from git_stage_batch.state import batch_exists, get_state_directory_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit with a file
    (repo / "test.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestSkipToBatch:
    """Test skip --to batch operations."""

    def test_skip_to_batch_auto_creates_batch(self, temp_git_repo):
        """Test that skip --to auto-creates batch if it doesn't exist."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Skip to non-existent batch
        command_skip_to_batch("my-batch")

        # Batch should now exist
        assert batch_exists("my-batch")

        # Clean up
        command_stop()

    def test_skip_to_batch_saves_file_content(self, temp_git_repo):
        """Test that skip --to saves current file content to batch."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Skip to batch
        command_skip_to_batch("test-batch")

        # Batch should contain the file with modified content
        files = list_batch_files("test-batch")
        assert "test.txt" in files

        # Content should match working tree
        saved_content = read_file_from_batch("test-batch", "test.txt")
        assert saved_content == "line1\nmodified\nline3\n"

        # Clean up
        command_stop()

    def test_skip_to_batch_leaves_working_tree_unchanged(self, temp_git_repo):
        """Test that skip --to doesn't modify the working tree."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")
        original_content = test_file.read_text()

        # Start session and skip to batch
        command_start()
        command_skip_to_batch("test-batch")

        # Working tree should be unchanged
        assert test_file.read_text() == original_content

        # Clean up
        command_stop()

    def test_skip_to_batch_marks_hunk_as_processed(self, temp_git_repo):
        """Test that skip --to marks hunk as processed in blocklist."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Skip to batch
        command_skip_to_batch("test-batch")

        # Trying to process again should show no hunks
        from git_stage_batch.state import get_block_list_file_path, read_text_file_contents
        blocklist = read_text_file_contents(get_block_list_file_path())
        assert len(blocklist.strip().splitlines()) > 0

        # Clean up
        command_stop()


class TestDiscardToBatch:
    """Test discard --to batch operations."""

    def test_discard_to_batch_auto_creates_batch(self, temp_git_repo):
        """Test that discard --to auto-creates batch if it doesn't exist."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Discard to non-existent batch
        command_discard_to_batch("my-batch")

        # Batch should now exist
        assert batch_exists("my-batch")

        # Clean up
        command_stop()

    def test_discard_to_batch_saves_file_content(self, temp_git_repo):
        """Test that discard --to saves current file content to batch."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Discard to batch
        command_discard_to_batch("test-batch")

        # Batch should contain the file with modified content
        files = list_batch_files("test-batch")
        assert "test.txt" in files

        # Content should be the modified version
        saved_content = read_file_from_batch("test-batch", "test.txt")
        assert saved_content == "line1\nmodified\nline3\n"

        # Clean up
        command_stop()

    def test_discard_to_batch_removes_from_working_tree(self, temp_git_repo):
        """Test that discard --to removes changes from working tree."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Discard to batch
        command_discard_to_batch("test-batch")

        # Working tree should be reverted to original
        assert test_file.read_text() == "line1\nline2\nline3\n"

        # Clean up
        command_stop()

    def test_discard_to_batch_saves_before_discarding(self, temp_git_repo):
        """Test that discard --to saves content before removing it."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Discard to batch
        command_discard_to_batch("recovery-batch")

        # Batch should have the modified content
        saved_content = read_file_from_batch("recovery-batch", "test.txt")
        assert saved_content == "line1\nmodified\nline3\n"

        # Working tree should be clean
        assert test_file.read_text() == "line1\nline2\nline3\n"

        # Clean up
        command_stop()

    def test_discard_to_batch_marks_hunk_as_processed(self, temp_git_repo):
        """Test that discard --to marks hunk as processed in blocklist."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start session
        command_start()

        # Discard to batch
        command_discard_to_batch("test-batch")

        # Hunk should be in blocklist
        from git_stage_batch.state import get_block_list_file_path, read_text_file_contents
        blocklist = read_text_file_contents(get_block_list_file_path())
        assert len(blocklist.strip().splitlines()) > 0

        # Clean up
        command_stop()
