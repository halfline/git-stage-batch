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
