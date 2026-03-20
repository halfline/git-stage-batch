"""Tests for apply command (apply --from BATCH)."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.batch import list_batch_files
from git_stage_batch.commands import (
    command_apply_from_batch,
    command_skip_to_batch,
    command_start,
    command_stop,
)
from git_stage_batch.state import CommandError, batch_exists


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


class TestApplyFromBatch:
    """Test apply --from batch operations."""

    def test_apply_from_batch_modifies_working_tree(self, temp_git_repo):
        """Test that apply --from applies changes to working tree."""
        # Create a batch with changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Reset working tree to original
        test_file.write_text("line1\nline2\nline3\n")

        # Apply batch to working tree
        command_apply_from_batch("test-batch")

        # Working tree should have batch changes
        assert test_file.read_text() == "modified1\nmodified2\nmodified3\n"

    def test_apply_from_batch_does_not_stage(self, temp_git_repo):
        """Test that apply --from doesn't stage changes to index."""
        # Create a batch with changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified1\nmodified2\nmodified3\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Reset working tree
        test_file.write_text("line1\nline2\nline3\n")

        # Apply batch
        command_apply_from_batch("test-batch")

        # Index should be clean (no staged changes)
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # Working tree should have changes
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in diff_result.stdout

    def test_apply_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test that apply --from fails gracefully for non-existent batch."""
        with pytest.raises(CommandError):
            command_apply_from_batch("nonexistent-batch")

    def test_apply_from_batch_with_multiple_files(self, temp_git_repo):
        """Test that apply --from applies changes to multiple files."""
        # Create multiple files with changes
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify both
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")

        # Save to batch
        command_start()
        command_skip_to_batch("multi-batch")
        # Skip second file too (need to advance)
        command_skip_to_batch("multi-batch")
        command_stop()

        # Reset working tree
        file1.write_text("content1\n")
        file2.write_text("content2\n")

        # Apply batch
        command_apply_from_batch("multi-batch")

        # Both files should have changes applied
        assert file1.read_text() == "modified1\n"
        assert file2.read_text() == "modified2\n"

    def test_apply_from_batch_with_line_ids(self, temp_git_repo):
        """Test that apply --from --line applies only selected lines."""
        # Create a batch with insertions (more realistic for line-level operations)
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "--amend", "-m", "Update initial"], cwd=temp_git_repo, check=True)

        # Add two new lines
        test_file.write_text("line1\nNEW1\nline2\nNEW2\nline3\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Reset working tree
        test_file.write_text("line1\nline2\nline3\n")

        # Apply only the first insertion (ID 1)
        command_apply_from_batch("test-batch", line_ids="1")

        # Verify only the first new line was added
        content = test_file.read_text()
        assert content == "line1\nNEW1\nline2\nline3\n"


class TestApplyIntegration:
    """Integration tests for apply command."""

    def test_apply_after_discard_restores_changes(self, temp_git_repo):
        """Test using apply to restore changes that were saved then discarded."""
        # Modify file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("important_change\nline2\nline3\n")

        # Save to batch then discard
        command_start()
        from git_stage_batch.commands import command_discard_to_batch
        command_discard_to_batch("backup")
        command_stop()

        # File should be reverted
        assert test_file.read_text() == "line1\nline2\nline3\n"

        # Apply batch to restore
        command_apply_from_batch("backup")

        # Change should be restored
        assert test_file.read_text() == "important_change\nline2\nline3\n"

    def test_apply_vs_include_difference(self, temp_git_repo):
        """Test that apply modifies working tree only while include modifies both."""
        # Create a batch
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("modified\nline2\nline3\n")

        command_start()
        command_skip_to_batch("test-batch")
        command_stop()

        # Reset
        test_file.write_text("line1\nline2\nline3\n")

        # Apply to working tree only
        command_apply_from_batch("test-batch")

        # Working tree should have changes
        assert test_file.read_text() == "modified\nline2\nline3\n"

        # Index should be clean (apply doesn't stage)
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # Now reset and try include instead
        test_file.write_text("line1\nline2\nline3\n")

        from git_stage_batch.commands import command_include_from_batch
        command_include_from_batch("test-batch")

        # Index should have changes (include stages)
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in diff_result.stdout

        # Working tree should ALSO have changes (include updates both)
        assert test_file.read_text() == "modified\nline2\nline3\n"
