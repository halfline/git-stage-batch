"""Tests for selected hunk filtering."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.core.line_selection import write_line_ids_file
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.selected_change.hunk_filtering import (
    apply_line_level_batch_filter_to_cached_hunk,
)
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_processed_batch_ids_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    ensure_state_directory_exists()

    return repo


def test_apply_line_level_batch_filter_returns_false_without_batched_ids(
    temp_git_repo,
):
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    test_file.write_text("changed1\nchanged2\nchanged3\n")

    command_start()
    fetch_next_change()
    write_line_ids_file(get_processed_batch_ids_file_path(), set())

    assert apply_line_level_batch_filter_to_cached_hunk() is False


def test_apply_line_level_batch_filter_returns_true_without_cached_hunk(
    temp_git_repo,
):
    assert apply_line_level_batch_filter_to_cached_hunk() is True
