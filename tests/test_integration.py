"""Integration tests for cross-feature interactions.

These tests validate that features work together correctly, covering scenarios
like state transitions, session lifecycle, and edge cases that span multiple
features.
"""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands import command_start, command_stop
from git_stage_batch.state import get_state_directory_path


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


class TestSessionLifecycle:
    """Test basic session lifecycle: start and stop."""

    def test_start_creates_state_directory(self, temp_git_repo):
        """Test that start creates the state directory."""
        # State shouldn't exist initially
        assert not get_state_directory_path().exists()

        # Start session
        command_start()
        assert get_state_directory_path().exists()

        # Clean up
        command_stop()

    def test_stop_removes_state_directory(self, temp_git_repo):
        """Test that stop removes the state directory."""
        # Start session
        command_start()
        assert get_state_directory_path().exists()

        # Stop session
        command_stop()
        assert not get_state_directory_path().exists()

    def test_stop_without_session_succeeds(self, temp_git_repo, capsys):
        """Test that stopping when no session exists still prints success."""
        # No session active
        assert not get_state_directory_path().exists()

        # Stop should not error
        command_stop()

        # Should print success message
        captured = capsys.readouterr()
        assert "State cleared" in captured.out

    def test_multiple_starts_are_idempotent(self, temp_git_repo):
        """Test that calling start multiple times is safe."""
        # First start
        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        # Second start should not error
        command_start()
        assert state_dir.exists()

        # Clean up
        command_stop()

    def test_session_state_directory_persists_until_stop(self, temp_git_repo):
        """Test that the state directory persists across commands until stop is called."""
        # Start session
        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        # Simulate doing other git operations
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)

        # State should still exist
        assert state_dir.exists()

        # Only stop should remove it
        command_stop()
        assert not state_dir.exists()
