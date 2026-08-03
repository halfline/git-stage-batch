"""Tests for selected-change loading and stale-cache validation."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.selected_change.file_changes import load_selected_mode_change
from git_stage_batch.data.selected_change.loading import require_selected_hunk
from git_stage_batch.data.selected_change.store import (
    SelectedChangeKind,
    write_selected_change_kind,
)
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_selected_mode_change_json_path,
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


def test_require_selected_hunk_exits_when_no_hunk_cached(temp_git_repo):
    with pytest.raises(CommandError) as exc_info:
        require_selected_hunk()

    assert "No selected hunk" in exc_info.value.message


def test_require_selected_hunk_exits_when_hunk_is_stale(temp_git_repo):
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("content\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    test_file.write_text("modified\n")
    fetch_next_change()

    test_file.write_text("different\n")

    with pytest.raises(CommandError) as exc_info:
        require_selected_hunk()

    assert "stale" in exc_info.value.message.lower()


def test_require_selected_hunk_succeeds_when_hunk_is_fresh(temp_git_repo):
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("content\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    test_file.write_text("modified\n")
    fetch_next_change()

    require_selected_hunk()


def test_load_selected_mode_rejects_non_string_paired_path(temp_git_repo):
    """Corrupt rename-pair metadata must not escape into path operations."""
    write_selected_change_kind(SelectedChangeKind.MODE)
    get_selected_mode_change_json_path().write_text(
        '{"file_path":"new.sh","old_mode":"100644",'
        '"new_mode":"100755","index_path":7}'
    )

    assert load_selected_mode_change() is None
