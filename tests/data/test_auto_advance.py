"""Tests for the automatic hunk selection preference."""

import subprocess

import pytest

from git_stage_batch.data.auto_advance import (
    read_auto_advance_default,
    write_auto_advance_default,
)
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_auto_advance_config_file_path,
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

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    ensure_state_directory_exists()
    return repo


def test_auto_advance_default_is_enabled_without_config(temp_git_repo):
    assert read_auto_advance_default() is True


def test_auto_advance_default_round_trips(temp_git_repo):
    write_auto_advance_default(False)

    assert read_auto_advance_default() is False

    write_auto_advance_default(True)

    assert read_auto_advance_default() is True


def test_auto_advance_default_ignores_unknown_config(temp_git_repo):
    write_text_file_contents(get_auto_advance_config_file_path(), "maybe\n")

    assert read_auto_advance_default() is True
