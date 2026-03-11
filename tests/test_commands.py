"""Tests for command implementations."""

import subprocess

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

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCommandStart:
    """Tests for start command."""

    def test_start_creates_state_directory(self, temp_git_repo):
        """Test that start creates the state directory."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        # Create a change so start doesn't exit
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()

        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_start_idempotent(self, temp_git_repo):
        """Test that start can be called multiple times."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        command_start()  # Should not raise

        state_dir = get_state_directory_path()
        assert state_dir.exists()


class TestCommandStop:
    """Tests for stop command."""

    def test_stop_removes_state_directory(self, temp_git_repo, capsys):
        """Test that stop removes the state directory."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()
        assert state_dir.exists()

        command_stop()

        assert not state_dir.exists()
        captured = capsys.readouterr()
        assert "State cleared" in captured.out

    def test_stop_when_no_state_exists(self, temp_git_repo, capsys):
        """Test that stop works when no state directory exists."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        command_stop()  # Should not raise

        captured = capsys.readouterr()
        assert "State cleared" in captured.out
