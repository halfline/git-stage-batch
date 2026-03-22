"""Tests for git command execution utilities."""

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import (
    get_git_repository_root_path,
    require_git_repository,
    run_git_command,
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


class TestRunGitCommand:
    """Tests for run_git_command function."""

    def test_successful_command_returns_result(self, temp_git_repo):
        """Test that successful git command returns CompletedProcess."""
        result = run_git_command(["status", "--short"])

        assert result.returncode == 0
        assert isinstance(result.stdout, str)

    def test_failed_command_with_check_raises(self, temp_git_repo):
        """Test that failed command with check=True raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-command"])

    def test_failed_command_without_check_returns_result(self, temp_git_repo):
        """Test that failed command with check=False returns result."""
        result = run_git_command(["invalid-command"], check=False)

        assert result.returncode != 0

    def test_text_output_returns_strings(self, temp_git_repo):
        """Test that text_output=True returns string output."""
        result = run_git_command(["status"], text_output=True)

        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)

    def test_captures_stdout(self, temp_git_repo):
        """Test that stdout is captured."""
        result = run_git_command(["rev-parse", "--git-dir"])

        assert ".git" in result.stdout


class TestRequireGitRepository:
    """Tests for require_git_repository function."""

    def test_succeeds_in_git_repository(self, temp_git_repo):
        """Test that function succeeds when inside a git repository."""
        # Should not raise
        require_git_repository()

    def test_exits_outside_git_repository(self, tmp_path, monkeypatch):
        """Test that function exits with error outside git repository."""
        # Change to non-git directory
        monkeypatch.chdir(tmp_path)

        with pytest.raises(CommandError):
            require_git_repository()


class TestGetGitRepositoryRootPath:
    """Tests for get_git_repository_root_path function."""

    def test_returns_repository_root(self, temp_git_repo):
        """Test that function returns the repository root path."""
        root = get_git_repository_root_path()

        assert isinstance(root, Path)
        assert root.is_absolute()
        assert (root / ".git").exists()

    def test_returns_same_path_from_subdirectory(self, temp_git_repo, monkeypatch):
        """Test that function returns root even from subdirectory."""
        # Create subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        root = get_git_repository_root_path()

        assert root == temp_git_repo
