"""Tests for start-time staged change normalization."""

import subprocess

import pytest

from git_stage_batch.commands.abort import command_abort
from git_stage_batch.commands.check_unstaged import command_check_unstaged
from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.discard import command_discard
from git_stage_batch.commands.selection.selected_change_display import show_selected_change
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.core.models import LineLevelChange, RenameChange, TextFileDeletionChange
from git_stage_batch.data.selected_change.loading import load_selected_change
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import get_staged_deletions_file_path, get_staged_renames_file_path


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


def _stage_rename(repo, *, new_content: str = "line 1\nline 2\n") -> None:
    (repo / "old.txt").rename(repo / "new.txt")
    (repo / "new.txt").write_text(new_content)
    subprocess.run(["git", "add", "-A"], check=True, cwd=repo, capture_output=True)


def _stage_deletion(repo, file_path: str = "old.txt") -> None:
    (repo / file_path).unlink()
    subprocess.run(["git", "add", "-A"], check=True, cwd=repo, capture_output=True)


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


def test_start_exposes_staged_rename_as_rename_selection(rename_repo, capsys):
    _stage_rename(rename_repo)

    command_start(quiet=True)
    show_selected_change()

    assert get_staged_renames_file_path().exists()
    assert _cached_name_status(rename_repo) == ""
    selected_change = load_selected_change()
    assert isinstance(selected_change, RenameChange)
    assert selected_change.old_path == "old.txt"
    assert selected_change.new_path == "new.txt"
    assert "old.txt -> new.txt" in capsys.readouterr().out


def test_start_exposes_unstaged_rename_as_rename_selection(rename_repo):
    _rename_without_staging(rename_repo)

    command_start(quiet=True)

    selected_change = load_selected_change()
    assert isinstance(selected_change, RenameChange)
    assert selected_change.old_path == "old.txt"
    assert selected_change.new_path == "new.txt"


def test_discard_unstaged_rename_preserves_index(rename_repo):
    """Rename discard restores paths from the index without rewriting it."""
    _rename_without_staging(rename_repo)
    command_start(quiet=True)

    command_discard(quiet=True)

    assert (rename_repo / "old.txt").read_text() == "line 1\nline 2\n"
    assert not (rename_repo / "new.txt").exists()
    assert _cached_name_status(rename_repo) == ""
    assert _uncached_name_status(rename_repo) == ""


def test_start_exposes_staged_deletion_as_deleted_line_selection(rename_repo):
    _stage_deletion(rename_repo)

    command_start(quiet=True)

    assert get_staged_deletions_file_path().exists()
    assert _cached_name_status(rename_repo) == ""
    assert _uncached_name_status(rename_repo).strip() == "D\told.txt"
    selected_change = load_selected_change()
    assert isinstance(selected_change, LineLevelChange)
    assert selected_change.path == "old.txt"
    assert {line.kind for line in selected_change.lines if line.id is not None} == {"-"}


def test_include_staged_deletion_lines_then_path_removal(rename_repo):
    _stage_deletion(rename_repo)

    command_start(quiet=True)
    command_include(quiet=True)

    assert _cached_name_status(rename_repo).strip() == "M\told.txt"
    assert _index_content(rename_repo, "old.txt") == ""
    assert _uncached_name_status(rename_repo).strip() == "D\told.txt"
    selected_change = load_selected_change()
    assert isinstance(selected_change, TextFileDeletionChange)
    assert selected_change.path() == "old.txt"

    command_include(quiet=True)

    assert _cached_name_status(rename_repo).strip() == "D\told.txt"


def test_include_selected_rename_stages_rename_only_and_leaves_edits_unstaged(rename_repo):
    _rename_without_staging(rename_repo, new_content="line 1\nline 2\nline 3\n")

    command_start(quiet=True)
    command_include(quiet=True, auto_advance=False)

    assert _cached_name_status(rename_repo).strip() == "R100\told.txt\tnew.txt"
    assert _index_content(rename_repo, "new.txt") == "line 1\nline 2\n"
    assert _uncached_name_status(rename_repo).strip() == "M\tnew.txt"


def test_stop_restores_untouched_start_time_staged_rename(rename_repo):
    _stage_rename(rename_repo)

    command_start(quiet=True)
    command_stop()

    assert _cached_name_status(rename_repo).strip() == "R100\told.txt\tnew.txt"


def test_stop_restores_untouched_start_time_staged_deletion(rename_repo):
    _stage_deletion(rename_repo)

    command_start(quiet=True)
    command_stop()

    assert _cached_name_status(rename_repo).strip() == "D\told.txt"


def test_stop_preserves_staged_rename_content_after_workflow_use(rename_repo):
    _stage_rename(rename_repo)

    command_start(quiet=True)
    (rename_repo / "new.txt").write_text("line 1\nline 2\nline 3\n")
    subprocess.run(["git", "add", "-A"], check=True, cwd=rename_repo, capture_output=True)
    command_stop()

    assert _cached_name_status(rename_repo).strip().startswith("R")
    assert _index_content(rename_repo, "new.txt") == "line 1\nline 2\nline 3\n"


def test_stop_does_not_restore_rename_paths_changed_by_session_commit(rename_repo):
    _stage_rename(rename_repo)

    command_start(quiet=True)
    (rename_repo / "new.txt").write_text("line 1\nline 2\nline 3\n")
    subprocess.run(["git", "add", "-A"], check=True, cwd=rename_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Rename file"], check=True, cwd=rename_repo, capture_output=True)
    command_stop()

    assert _cached_name_status(rename_repo) == ""


def test_abort_restores_start_time_staged_rename(rename_repo):
    _stage_rename(rename_repo)

    command_start(quiet=True)
    command_abort()

    assert _cached_name_status(rename_repo).strip() == "R100\told.txt\tnew.txt"


def test_abort_restores_start_time_staged_deletion(rename_repo):
    _stage_deletion(rename_repo)

    command_start(quiet=True)
    command_abort()

    assert _cached_name_status(rename_repo).strip() == "D\told.txt"


def test_check_unstaged_allows_clean_index(rename_repo):
    command_check_unstaged()


def test_check_unstaged_allows_staged_rename(rename_repo):
    _stage_rename(rename_repo)

    command_check_unstaged()


def test_check_unstaged_allows_staged_deletion(rename_repo):
    _stage_deletion(rename_repo)

    command_check_unstaged()


def test_check_unstaged_allows_staged_deletion_mixed_with_rename(rename_repo):
    _stage_rename(rename_repo)
    _stage_deletion(rename_repo, "other.txt")

    command_check_unstaged()


def test_check_unstaged_rejects_non_rename_staged_content(rename_repo):
    (rename_repo / "other.txt").write_text("changed\n")
    subprocess.run(["git", "add", "other.txt"], check=True, cwd=rename_repo, capture_output=True)

    with pytest.raises(CommandError) as exc_info:
        command_check_unstaged()

    assert exc_info.value.exit_code == 2


def test_check_unstaged_rejects_rename_mixed_with_other_staged_content(rename_repo):
    _stage_rename(rename_repo)
    (rename_repo / "other.txt").write_text("changed\n")
    subprocess.run(["git", "add", "other.txt"], check=True, cwd=rename_repo, capture_output=True)

    with pytest.raises(CommandError) as exc_info:
        command_check_unstaged()

    assert exc_info.value.exit_code == 2
