"""Tests for state management and filesystem utilities."""

import subprocess

import pytest

from git_stage_batch.state import CommandError, exit_with_error


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
