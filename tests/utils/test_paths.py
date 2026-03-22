"""Tests for state directory path utilities."""

import subprocess

import pytest

from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
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


class TestGetStateDirectoryPath:
    """Tests for get_state_directory_path function."""

    def test_get_state_directory_path(self, temp_git_repo):
        """Test getting the state directory path."""
        state_dir = get_state_directory_path()
        assert state_dir == temp_git_repo / ".git" / "git-stage-batch"


class TestEnsureStateDirectoryExists:
    """Tests for ensure_state_directory_exists function."""

    def test_ensure_state_directory_exists_creates_directory(self, temp_git_repo):
        """Test that ensure_state_directory_exists creates the directory."""
        state_dir = get_state_directory_path()
        assert not state_dir.exists()

        ensure_state_directory_exists()

        assert state_dir.exists()
        assert state_dir.is_dir()

    def test_ensure_state_directory_exists_idempotent(self, temp_git_repo):
        """Test that ensure_state_directory_exists is idempotent."""
        ensure_state_directory_exists()
        ensure_state_directory_exists()  # Should not raise

        state_dir = get_state_directory_path()
        assert state_dir.exists()
