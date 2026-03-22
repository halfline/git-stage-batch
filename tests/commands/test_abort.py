"""Tests for abort command."""

import subprocess

import pytest

from git_stage_batch.commands.abort import command_abort
from git_stage_batch.commands.discard import command_discard
from git_stage_batch.commands.start import command_start
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import append_file_path_to_file
from git_stage_batch.utils.paths import get_auto_added_files_file_path, get_state_directory_path


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


class TestCommandAbort:
    """Tests for abort command."""

    def test_abort_no_session(self, temp_git_repo):
        """Test abort when no session exists."""
        # Should error when no abort state exists
        with pytest.raises(CommandError) as exc_info:
            command_abort()

        assert "No session to abort" in exc_info.value.message

    def test_abort_restores_working_tree(self, temp_git_repo):
        """Test that abort restores working tree state from session start."""
        # Create a file with uncommitted changes
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nUncommitted change\n")

        # Start session (this saves the uncommitted state in stash)
        command_start()

        # Make more changes and discard them
        readme.write_text("# Test\nAnother change\n")
        command_discard()

        # File should be back to original committed state
        assert readme.read_text() == "# Test\n"

        # Abort should restore the uncommitted changes from session start
        command_abort()

        # File should have the uncommitted changes from before session
        assert readme.read_text() == "# Test\nUncommitted change\n"

    def test_abort_undoes_commits(self, temp_git_repo):
        """Test that abort undoes commits made during session."""
        # Get initial HEAD
        initial_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Create changes and start session
        (temp_git_repo / "README.md").write_text("# Test\nNew content\n")

        # Start session
        command_start()

        # Make a change and commit it
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Session commit"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify HEAD moved
        new_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert new_head != initial_head

        # Abort should restore HEAD
        command_abort()

        restored_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert restored_head == initial_head

    def test_abort_clears_state(self, temp_git_repo):
        """Test that abort clears all session state."""
        # Create changes and start
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        command_abort()

        assert not state_dir.exists()

    def test_abort_with_staged_changes_before_session(self, temp_git_repo):
        """Test abort restores staged changes from before session."""
        # Create and stage a new file before session
        new_file = temp_git_repo / "new.txt"
        new_file.write_text("new content\n")
        subprocess.run(["git", "add", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Verify it's staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

        # Create unstaged changes so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Unstage and delete the file
        subprocess.run(["git", "reset", "new.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        new_file.unlink()

        # Abort should restore the staged file
        command_abort()

        # File should exist again
        assert new_file.exists()
        assert new_file.read_text() == "new content\n"

        # And should be staged
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

    def test_abort_resets_auto_added_files(self, temp_git_repo):
        """Test that abort resets auto-added files."""
        # Create an untracked file
        new_file = temp_git_repo / "untracked.txt"
        new_file.write_text("untracked content\n")

        # Create a diff so start has something to work with
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        # Start session
        command_start()

        # Simulate auto-add by adding with -N and tracking it
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        # Record it in auto-added list
        append_file_path_to_file(get_auto_added_files_file_path(), "untracked.txt")

        # Verify it's in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "untracked.txt" in result.stdout

        # Abort should reset the auto-added file
        command_abort()

        # File should no longer be in index
        result = subprocess.run(
            ["git", "ls-files", "untracked.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""
