"""Tests for persisted suggest-fixup state."""

import subprocess

import pytest

from git_stage_batch.data.suggest_fixup_state import (
    clear_suggest_fixup_state,
    read_suggest_fixup_state,
    suggest_fixup_state_should_reset,
    write_suggest_fixup_state,
)
from git_stage_batch.utils.paths import get_suggest_fixup_state_file_path


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

    return repo


def test_read_state_when_no_file_exists(temp_git_repo):
    """Missing state file should read as None."""
    state = read_suggest_fixup_state()

    assert state is None


def test_write_and_read_state(temp_git_repo):
    """Persisted state should round-trip through JSON."""
    test_state = {
        "hunk_hash": "abc123",
        "line_ids": [1, 2, 3],
        "boundary": "@{upstream}",
        "file_path": "test.py",
        "min_line": 10,
        "max_line": 20,
        "last_shown_commit": "def456",
        "iteration": 1,
    }

    write_suggest_fixup_state(test_state)
    loaded_state = read_suggest_fixup_state()

    assert loaded_state == test_state


def test_clear_state(temp_git_repo):
    """Clearing state should remove the state file."""
    write_suggest_fixup_state({"hunk_hash": "abc123"})

    assert get_suggest_fixup_state_file_path().exists()

    clear_suggest_fixup_state()

    assert not get_suggest_fixup_state_file_path().exists()
    assert read_suggest_fixup_state() is None


def test_should_reset_when_no_state_exists(temp_git_repo):
    """Reset check should be true when no state exists."""
    should_reset = suggest_fixup_state_should_reset(
        "hash1", [1, 2], "@{upstream}", "test.py", 10, 20
    )

    assert should_reset is True


def test_should_reset_when_hunk_hash_changes(temp_git_repo):
    """Reset check should be true when hunk hash changes."""
    write_suggest_fixup_state({
        "hunk_hash": "hash1",
        "line_ids": [1, 2],
        "boundary": "@{upstream}",
        "file_path": "test.py",
        "min_line": 10,
        "max_line": 20,
    })

    should_reset = suggest_fixup_state_should_reset(
        "hash2", [1, 2], "@{upstream}", "test.py", 10, 20
    )

    assert should_reset is True


def test_should_reset_when_line_ids_change(temp_git_repo):
    """Reset check should be true when line IDs change."""
    write_suggest_fixup_state({
        "hunk_hash": "hash1",
        "line_ids": [1, 2],
        "boundary": "@{upstream}",
        "file_path": "test.py",
        "min_line": 10,
        "max_line": 20,
    })

    should_reset = suggest_fixup_state_should_reset(
        "hash1", [1, 2, 3], "@{upstream}", "test.py", 10, 20
    )

    assert should_reset is True


def test_should_reset_when_boundary_changes(temp_git_repo):
    """Reset check should be true when boundary changes."""
    write_suggest_fixup_state({
        "hunk_hash": "hash1",
        "line_ids": [1, 2],
        "boundary": "@{upstream}",
        "file_path": "test.py",
        "min_line": 10,
        "max_line": 20,
    })

    should_reset = suggest_fixup_state_should_reset(
        "hash1", [1, 2], "HEAD~5", "test.py", 10, 20
    )

    assert should_reset is True


def test_should_not_reset_when_parameters_match(temp_git_repo):
    """Reset check should be false when all parameters match."""
    write_suggest_fixup_state({
        "hunk_hash": "hash1",
        "line_ids": [1, 2],
        "boundary": "@{upstream}",
        "file_path": "test.py",
        "min_line": 10,
        "max_line": 20,
    })

    should_reset = suggest_fixup_state_should_reset(
        "hash1", [1, 2], "@{upstream}", "test.py", 10, 20
    )

    assert should_reset is False
