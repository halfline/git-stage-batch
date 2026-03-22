"""Tests for again command."""

import subprocess

import pytest

from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.start import command_start
from git_stage_batch.utils.paths import get_state_directory_path


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


class TestCommandAgain:
    """Tests for again command."""

    def test_again_clears_and_recreates_state(self, temp_git_repo):
        """Test that again clears and recreates the state directory."""
        # Create changes for start to process
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")

        command_start()
        state_dir = get_state_directory_path()

        # Create a marker file
        marker = state_dir / "marker.txt"
        marker.write_text("test")
        assert marker.exists()

        command_again()

        # Directory should still exist but marker should be gone
        assert state_dir.exists()
        assert not marker.exists()

    def test_again_when_no_state_exists(self, temp_git_repo):
        """Test that again works when no state directory exists."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        command_again()  # Should not raise

        assert state_dir.exists()
