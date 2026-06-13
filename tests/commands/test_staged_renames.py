"""Tests for rename selections in live sessions."""

import subprocess

import pytest

from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.start import command_start
from git_stage_batch.core.models import RenameChange
from git_stage_batch.data.hunk_tracking import load_selected_change


@pytest.fixture
def rename_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    (repo / "old.txt").write_text("line 1\nline 2\n")
    (repo / "other.txt").write_text("original\n")
    subprocess.run(["git", "add", "."], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=repo, capture_output=True)

    return repo


def _rename_without_staging(repo, *, new_content: str = "line 1\nline 2\n") -> None:
    (repo / "old.txt").rename(repo / "new.txt")
    (repo / "new.txt").write_text(new_content)


def _cached_name_status(repo) -> str:
    return subprocess.run(
        ["git", "diff", "--cached", "--name-status", "-M"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def _uncached_name_status(repo) -> str:
    return subprocess.run(
        ["git", "diff", "--name-status", "-M"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def _index_content(repo, file_path: str) -> str:
    return subprocess.run(
        ["git", "show", f":{file_path}"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def test_start_exposes_unstaged_rename_as_rename_selection(rename_repo):
    _rename_without_staging(rename_repo)

    command_start(quiet=True)

    selected_change = load_selected_change()
    assert isinstance(selected_change, RenameChange)
    assert selected_change.old_path == "old.txt"
    assert selected_change.new_path == "new.txt"


def test_include_selected_rename_stages_rename_only_and_leaves_edits_unstaged(rename_repo):
    _rename_without_staging(rename_repo, new_content="line 1\nline 2\nline 3\n")

    command_start(quiet=True)
    command_include(quiet=True, auto_advance=False)

    assert _cached_name_status(rename_repo).strip() == "R100\told.txt\tnew.txt"
    assert _index_content(rename_repo, "new.txt") == "line 1\nline 2\n"
    assert _uncached_name_status(rename_repo).strip() == "M\tnew.txt"
