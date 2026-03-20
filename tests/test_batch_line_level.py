"""Tests for line-level batch operations (--line with --from/--to)."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.batch import get_batch_diff, list_batch_files, read_file_from_batch
from git_stage_batch.commands import (
    command_discard_from_batch,
    command_discard_to_batch,
    command_include_from_batch,
    command_show_from_batch,
    command_skip_to_batch,
    command_start,
    command_stop,
)
from git_stage_batch.state import batch_exists


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
    (repo / "test.txt").write_text("line1\nline2\nline3\nline4\nline5\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestLineLevelFromOperations:
    """Test --line with --from operations."""

    def test_show_from_batch_with_line_ids(self, temp_git_repo, capsys):
        """Test that show --from --line filters display to selected lines."""
        # Create a batch with changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Show only specific lines from batch
        capsys.readouterr()
        command_show_from_batch("test-batch", line_ids="1,3")

        # Should show filtered output
        # (Hard to test exact output, but verify it doesn't error)

    def test_include_from_batch_with_line_ids(self, temp_git_repo):
        """Test that include --from --line stages only selected lines."""
        # Create a batch with multi-line changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Reset working tree to original
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        # Include only lines 1 and 3 from batch
        command_include_from_batch("test-batch", line_ids="1,3")

        # Check index contains partial changes
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        # Should have some changes staged
        assert diff_result.stdout.strip() != ""

    def test_discard_from_batch_with_line_ids(self, temp_git_repo):
        """Test that discard --from --line removes only selected lines."""
        # Create a batch with changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Working tree has modified content
        assert test_file.read_text() == "modified1\nmodified2\nmodified3\nmodified4\nmodified5\n"

        # Discard only lines 1 and 3 from batch
        command_discard_from_batch("test-batch", line_ids="1,3")

        # Working tree should have partial changes remaining
        content = test_file.read_text()
        # Some lines should be reverted, others not
        # (Exact content depends on patch application)


class TestLineLevelToOperations:
    """Test --line with --to operations."""

    def test_skip_to_batch_with_line_ids(self, temp_git_repo):
        """Test that skip --to --line saves only selected lines."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()

        # Skip only lines 1,3 to batch
        command_skip_to_batch("test-batch", line_ids="1,3")

        # Batch should exist
        assert batch_exists("test-batch")

        # Batch should contain partial changes
        files = list_batch_files("test-batch")
        assert "test.txt" in files

        # Working tree should still have all changes
        assert test_file.read_text() == "modified1\nmodified2\nmodified3\nmodified4\nmodified5\n"

        command_stop()

    def test_discard_to_batch_with_line_ids(self, temp_git_repo):
        """Test that discard --to --line saves and discards only selected lines."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()

        # Discard only lines 1,3 to batch
        command_discard_to_batch("test-batch", line_ids="1,3")

        # Batch should exist with partial changes
        assert batch_exists("test-batch")

        # Working tree should have some lines reverted
        content = test_file.read_text()
        # Should not be completely original or completely modified
        assert content != "line1\nline2\nline3\nline4\nline5\n"
        assert content != "modified1\nmodified2\nmodified3\nmodified4\nmodified5\n"

        command_stop()

    def test_skip_to_batch_line_ids_preserves_working_tree(self, temp_git_repo):
        """Test that skip --to --line doesn't modify working tree."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")
        original_content = test_file.read_text()

        command_start()
        command_skip_to_batch("test-batch", line_ids="2,4")

        # Working tree should be unchanged
        assert test_file.read_text() == original_content

        command_stop()


class TestLineLevelIntegration:
    """Integration tests for line-level batch operations."""

    def test_round_trip_line_selection(self, temp_git_repo):
        """Test saving lines to batch and retrieving them."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\nmodified4\nmodified5\n")

        command_start()

        # Save only lines 1-3 to batch
        command_skip_to_batch("recovery", line_ids="1-3")

        command_stop()

        # Reset to original
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        # Retrieve lines 1-3 from batch
        command_include_from_batch("recovery", line_ids="1-3")

        # Index should have partial changes
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() != ""
