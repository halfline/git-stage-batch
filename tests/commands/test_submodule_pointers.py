"""Tests for live submodule pointer selections."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands.discard import command_discard, command_discard_file
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.batch.query import get_batch_commit_sha, read_batch_metadata
from git_stage_batch.commands.include import (
    command_include,
    command_include_file,
    command_include_to_batch,
)
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.redo import command_redo
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.show import command_show_file_list
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.commands.skip import command_skip
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.status import command_status
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.data.hunk_tracking import (
    SelectedChangeKind,
    get_selected_change_file_path,
    read_selected_change_kind,
)
from git_stage_batch.exceptions import CommandError


@pytest.fixture
def submodule_pointer_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    """Create a superproject with a modified submodule pointer."""
    repo = tmp_path / "repo"
    submodule = repo / "sub"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _configure_identity(repo)

    submodule.mkdir()
    _run(["git", "init"], cwd=submodule)
    _configure_identity(submodule)

    (submodule / "file.txt").write_text("one\n")
    _run(["git", "add", "file.txt"], cwd=submodule)
    _run(["git", "commit", "-m", "Add file"], cwd=submodule)
    old_oid = _git_stdout(["rev-parse", "HEAD"], cwd=submodule)

    (submodule / "file.txt").write_text("two\n")
    _run(["git", "commit", "-am", "Update file"], cwd=submodule)
    new_oid = _git_stdout(["rev-parse", "HEAD"], cwd=submodule)
    _run(["git", "checkout", "--detach", old_oid], cwd=submodule)

    (repo / "z.txt").write_text("base\n")
    _run(["git", "add", "z.txt"], cwd=repo)
    _run(["git", "update-index", "--add", "--cacheinfo", "160000", old_oid, "sub"], cwd=repo)
    _run(["git", "commit", "-m", "Add submodule pointer"], cwd=repo)

    _run(["git", "checkout", "--detach", new_oid], cwd=submodule)
    _run(["git", "config", "diff.ignoreSubmodules", "all"], cwd=repo)
    monkeypatch.chdir(repo)

    return repo, old_oid, new_oid


@pytest.fixture
def added_submodule_pointer_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Create a superproject with an added submodule pointer."""
    repo = tmp_path / "repo"
    submodule = repo / "sub"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _configure_identity(repo)
    (repo / "base.txt").write_text("base\n")
    _run(["git", "add", "base.txt"], cwd=repo)
    _run(["git", "commit", "-m", "Add base"], cwd=repo)

    submodule.mkdir()
    _run(["git", "init"], cwd=submodule)
    _configure_identity(submodule)
    (submodule / "file.txt").write_text("sub\n")
    _run(["git", "add", "file.txt"], cwd=submodule)
    _run(["git", "commit", "-m", "Add sub file"], cwd=submodule)
    new_oid = _git_stdout(["rev-parse", "HEAD"], cwd=submodule)
    _run(["git", "add", "-N", "sub"], cwd=repo)
    _run(["git", "config", "diff.ignoreSubmodules", "all"], cwd=repo)
    monkeypatch.chdir(repo)

    return repo, new_oid


@pytest.fixture
def deleted_submodule_pointer_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Create a superproject with a deleted submodule pointer."""
    repo = tmp_path / "repo"
    submodule = repo / "sub"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _configure_identity(repo)

    submodule.mkdir()
    _run(["git", "init"], cwd=submodule)
    _configure_identity(submodule)
    (submodule / "file.txt").write_text("sub\n")
    _run(["git", "add", "file.txt"], cwd=submodule)
    _run(["git", "commit", "-m", "Add sub file"], cwd=submodule)
    old_oid = _git_stdout(["rev-parse", "HEAD"], cwd=submodule)

    _run(["git", "update-index", "--add", "--cacheinfo", "160000", old_oid, "sub"], cwd=repo)
    _run(["git", "commit", "-m", "Add submodule pointer"], cwd=repo)
    submodule.rename(tmp_path / "sub-backup")
    _run(["git", "config", "diff.ignoreSubmodules", "all"], cwd=repo)
    monkeypatch.chdir(repo)

    return repo, old_oid


def test_start_shows_submodule_pointer_when_config_ignores_submodules(
    submodule_pointer_repo: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """start should show submodule pointer changes hidden by user config."""
    _repo, old_oid, new_oid = submodule_pointer_repo

    command_start()

    captured = capsys.readouterr()
    assert "sub :: Submodule pointer modified" in captured.out
    assert f"old {old_oid[:12]}" in captured.out
    assert f"new {new_oid[:12]}" in captured.out
    assert "gitlink" not in (captured.out + captured.err).lower()
    assert read_selected_change_kind() == SelectedChangeKind.GITLINK
    assert read_selected_change_kind().value == "submodule"
    assert get_selected_change_file_path() == "sub"


def test_start_shows_added_submodule_pointer(
    added_submodule_pointer_repo: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """start should handle intent-to-add submodule pointers."""
    _repo, new_oid = added_submodule_pointer_repo

    command_start()

    captured = capsys.readouterr()
    assert f"sub :: Submodule added at {new_oid[:12]}" in captured.out
    assert read_selected_change_kind() == SelectedChangeKind.GITLINK
    assert get_selected_change_file_path() == "sub"


def test_start_shows_deleted_submodule_pointer(
    deleted_submodule_pointer_repo: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """start should handle deleted submodule pointers."""
    _repo, old_oid = deleted_submodule_pointer_repo

    command_start()

    captured = capsys.readouterr()
    assert f"sub :: Submodule removed from {old_oid[:12]}" in captured.out
    assert read_selected_change_kind() == SelectedChangeKind.GITLINK
    assert get_selected_change_file_path() == "sub"


def test_status_reports_submodule_pointer_selection(
    submodule_pointer_repo: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """status should expose submodule pointer selections without Git terms."""
    command_start(quiet=True)

    command_status(porcelain=True)
    captured = capsys.readouterr()
    status = json.loads(captured.out)

    assert status["selected_change"]["kind"] == "submodule"
    assert status["selected_change"]["file"] == "sub"
    assert status["selected_change"]["change_type"] == "modified"
    assert "gitlink" not in captured.out.lower()


def test_skip_records_submodule_pointer_metadata(
    submodule_pointer_repo: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """skip should process submodule pointer changes atomically."""
    command_start(quiet=True)

    command_skip(quiet=True)
    command_status(porcelain=True)
    captured = capsys.readouterr()
    status = json.loads(captured.out)

    assert status["progress"]["skipped"] == 1
    assert status["skipped_hunks"][0]["type"] == "submodule"
    assert status["skipped_hunks"][0]["file"] == "sub"
    assert status["skipped_hunks"][0]["change_type"] == "modified"
    assert "gitlink" not in captured.out.lower()


def test_file_list_uses_submodule_pointer_wording(
    submodule_pointer_repo: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """file lists should describe submodule pointer entries."""
    command_start(quiet=True)
    capsys.readouterr()

    command_show_file_list(["sub"])

    captured = capsys.readouterr()
    assert "submodule pointer modified" in captured.out
    assert "gitlink" not in (captured.out + captured.err).lower()


def _configure_identity(repo: Path) -> None:
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)


def _run(arguments: list[str], *, cwd: Path) -> None:
    subprocess.run(arguments, cwd=cwd, check=True, capture_output=True)


def _git_stdout(arguments: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _cached_raw_diff(repo: Path) -> str:
    return _git_stdout(
        ["diff", "--cached", "--raw", "--abbrev=40", "--ignore-submodules=none"],
        cwd=repo,
    )


def _worktree_pointer_diff(repo: Path) -> str:
    return _git_stdout(
        ["diff", "--ignore-submodules=none", "--submodule=short", "--", "sub"],
        cwd=repo,
    )