"""Tests for start command."""

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import (
    get_abort_head_file_path,
    get_abort_stash_file_path,
    get_state_directory_path,
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

    def test_start_initializes_abort_state(self, temp_git_repo):
        """Test that start initializes abort state files."""
        # Create a change to make start succeed
        (temp_git_repo / "README.md").write_text("# Test\nModified\n")

        command_start()

        # Verify abort-head file was created with current HEAD
        abort_head_path = get_abort_head_file_path()
        assert abort_head_path.exists()
        saved_head = read_text_file_contents(abort_head_path).strip()
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert saved_head == current_head

        # Verify abort-stash file was created (may be empty if no tracked changes)
        abort_stash_path = get_abort_stash_file_path()
        assert abort_stash_path.exists()
