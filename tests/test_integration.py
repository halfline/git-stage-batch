"""Integration tests for cross-feature interactions.

These tests validate that features work together correctly, covering scenarios
like state transitions, session lifecycle, and edge cases that span multiple
features.
"""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands import (
    command_abort,
    command_discard,
    command_include,
    command_start,
    command_stop,
)
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


class TestAbortWorkflow:
    """Test abort functionality in various workflow scenarios."""

    def test_abort_restores_working_tree_after_discard(self, temp_git_repo):
        """Test that abort restores changes that were discarded."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add test file"], cwd=temp_git_repo, check=True)

        # Modify the file
        test_file.write_text("modified content\n")
        original_modified_content = test_file.read_text()

        # Start session and discard the change
        command_start()
        command_discard()

        # File should be reverted
        assert test_file.read_text() == "original content\n"

        # Abort should restore the modified content
        command_abort()
        assert test_file.read_text() == original_modified_content

        # Session state should be cleared
        assert not get_state_directory_path().exists()

    def test_abort_undoes_staged_changes(self, temp_git_repo):
        """Test that abort removes changes from the index."""
        # Create changes
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")

        # Start and include (stage) the change
        command_start()
        command_include()

        # Change should be staged
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in diff_result.stdout

        # Abort should unstage it
        command_abort()

        # Index should be clean
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # But working tree should still have the change
        assert "modified" in test_file.read_text()

    def test_abort_with_both_staged_and_discarded_changes(self, temp_git_repo):
        """Test abort with complex state: some changes staged, some discarded."""
        # Create two files
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("content1\n")
        file2.write_text("content2\n")
        subprocess.run(["git", "add", "."], cwd=temp_git_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=temp_git_repo, check=True)

        # Modify both files
        file1.write_text("modified1\n")
        file2.write_text("modified2\n")

        # Start session
        command_start()

        # Include first file (stages it)
        command_include()

        # Discard second file (reverts it)
        command_discard()

        # Verify state: file1 staged, file2 reverted
        assert file2.read_text() == "content2\n"
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified1" in diff_result.stdout

        # Abort should restore everything
        command_abort()

        # Index should be clean
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert diff_result.stdout.strip() == ""

        # Both files should have modifications back
        assert file1.read_text() == "modified1\n"
        assert file2.read_text() == "modified2\n"

    def test_abort_without_session_fails_gracefully(self, temp_git_repo, capsys):
        """Test that abort gives a clear error when no session exists."""
        # No session active
        assert not get_state_directory_path().exists()

        # Abort should error
        capsys.readouterr()
        with pytest.raises(SystemExit):
            command_abort()

        captured = capsys.readouterr()
        assert "No session to abort" in captured.err or "not found" in captured.err

    def test_abort_clears_session_state(self, temp_git_repo):
        """Test that abort removes the session state directory."""
        # Create changes and start session
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nmodified\nline3\n")
        command_start()

        # State should exist
        assert get_state_directory_path().exists()

        # Abort should remove state
        command_abort()
        assert not get_state_directory_path().exists()
