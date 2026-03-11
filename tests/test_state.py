"""Tests for state management and filesystem utilities."""

import subprocess

import pytest

from git_stage_batch.state import (
    CommandError,
    exit_with_error,
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


class TestErrorHandling:
    """Tests for error handling utilities."""

    def test_exit_with_error(self):
        """Test exit_with_error raises CommandError with message."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("Test error message")

        assert exc_info.value.message == "Test error message"
        assert exc_info.value.exit_code == 1

    def test_exit_with_error_custom_code(self):
        """Test exit_with_error with custom exit code."""
        with pytest.raises(CommandError) as exc_info:
            exit_with_error("Custom error", exit_code=42)

        assert exc_info.value.message == "Custom error"
        assert exc_info.value.exit_code == 42


class TestGitUtilities:
    """Tests for git command utilities."""

    def test_run_git_command_success(self, temp_git_repo):
        """Test running a successful git command."""
        result = run_git_command(["status", "--short"])
        assert result.returncode == 0
        assert isinstance(result.stdout, str)

    def test_run_git_command_failure(self, temp_git_repo):
        """Test running a failing git command."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-command"])

    def test_run_git_command_no_check(self, temp_git_repo):
        """Test running a command without checking return code."""
        result = run_git_command(["invalid-command"], check=False)
        assert result.returncode != 0

    def test_require_git_repository_success(self, temp_git_repo):
        """Test require_git_repository in a valid repo."""
        require_git_repository()  # Should not raise

    def test_require_git_repository_failure(self, tmp_path, monkeypatch):
        """Test require_git_repository outside a git repo."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(CommandError) as exc_info:
            require_git_repository()
        assert exc_info.value.exit_code == 128

    def test_get_git_repository_root_path(self, temp_git_repo):
        """Test getting the repository root path."""
        # Create a subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        import os
        os.chdir(subdir)

        root = get_git_repository_root_path()
        assert root == temp_git_repo
