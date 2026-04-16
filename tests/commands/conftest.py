"""Shared fixtures for command tests."""

import subprocess

import pytest

from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists


@pytest.fixture
def temp_git_repo_with_session(tmp_path, monkeypatch):
    """Create a temporary git repository with an initialized session.

    This fixture is for command tests that need batch operations,
    which require a session to be started (for batch source commits).
    """
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    # Create a test file with changes
    (tmp_path / "file.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "-N", "file.txt"], check=True)

    # Initialize session (needed for batch source creation)
    ensure_state_directory_exists()
    initialize_abort_state()

    return tmp_path
