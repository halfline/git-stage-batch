"""Tests for state directory path utilities."""

import subprocess

import pytest

from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_context_lines,
    get_context_lines_file_path,
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


class TestGetContextLines:
    """Tests for get_context_lines function."""

    def test_get_context_lines_default(self, temp_git_repo):
        """Test that get_context_lines returns 3 when file doesn't exist."""
        ensure_state_directory_exists()
        assert get_context_lines() == 3

    def test_get_context_lines_reads_file(self, temp_git_repo):
        """Test that get_context_lines reads value from file."""
        ensure_state_directory_exists()
        context_file = get_state_directory_path() / "context-lines"
        context_file.write_text("5\n")
        assert get_context_lines() == 5

    def test_get_context_lines_invalid_content(self, temp_git_repo):
        """Test that get_context_lines returns 3 for invalid content."""
        ensure_state_directory_exists()
        context_file = get_state_directory_path() / "context-lines"
        context_file.write_text("not-a-number\n")
        assert get_context_lines() == 3


class TestGetContextLinesFilePath:
    """Tests for get_context_lines_file_path function."""

    def test_get_context_lines_file_path(self, temp_git_repo):
        """Test getting the context lines file path."""
        context_file = get_context_lines_file_path()
        assert context_file == temp_git_repo / ".git" / "git-stage-batch" / "context-lines"
