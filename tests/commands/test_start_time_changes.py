"""Tests for start-time staged change normalization."""

import subprocess

import pytest

from git_stage_batch.commands.abort import command_abort
from git_stage_batch.commands.check_unstaged import command_check_unstaged
from git_stage_batch.commands.include import (
    command_include,
    command_include_file,
    command_include_to_batch,
)
from git_stage_batch.commands.skip import command_skip
from git_stage_batch.commands.discard import command_discard
from git_stage_batch.commands.selection.selected_change_display import show_selected_change
from git_stage_batch.commands.selection.selected_change_staging import (
    stage_file_mode_change,
)
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.core.models import FileModeChange, LineLevelChange, RenameChange
from git_stage_batch.batch.state.batch_names import batch_exists
from git_stage_batch.data.selected_change.loading import load_selected_change
from git_stage_batch.data.start_time_changes import list_staged_change_records
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


def test_staged_change_discovery_propagates_git_errors(monkeypatch):
    """Failed staged discovery must not look like an empty change list."""
    from git_stage_batch.data import start_time_changes

    monkeypatch.setattr(
        start_time_changes,
        "run_git_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["git", "diff", "--cached"],
            128,
            stdout=b"",
            stderr=b"fatal: malformed index",
        ),
    )

    with pytest.raises(subprocess.CalledProcessError) as error:
        list_staged_change_records()

    assert error.value.stderr == b"fatal: malformed index"


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


@pytest.mark.parametrize(
    "file_path",
    (":(top)foo", ":(glob)foo", "*.c", ":(exclude)foo"),
)
def test_start_normalizes_staged_deletion_of_literal_pathspec_name(
    rename_repo,
    file_path,
):
    """Start-time deletion probes must treat discovered paths literally."""
    path = rename_repo / file_path
    path.write_text("literal pathspec name\n")
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", file_path],
        check=True,
        cwd=rename_repo,
    )
    subprocess.run(
        ["git", "commit", "-qm", "Add literal pathspec name"],
        check=True,
        cwd=rename_repo,
    )
    path.unlink()
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", file_path],
        check=True,
        cwd=rename_repo,
    )

    command_start(quiet=True)

    assert get_staged_deletions_file_path().exists()
    assert _cached_name_status(rename_repo) == ""
    selected_change = load_selected_change()
    assert isinstance(selected_change, LineLevelChange)
    assert selected_change.path == file_path


def test_include_staged_deletion_removes_path_in_one_action(rename_repo):
    _stage_deletion(rename_repo)

    command_start(quiet=True)
    command_include(quiet=True)

    assert _cached_name_status(rename_repo).strip() == "D\told.txt"
    assert _uncached_name_status(rename_repo) == ""


def test_include_selected_rename_stages_rename_only_and_leaves_edits_unstaged(rename_repo):
    _rename_without_staging(rename_repo, new_content="line 1\nline 2\nline 3\n")

    command_start(quiet=True)
    command_include(quiet=True, auto_advance=False)

    assert _cached_name_status(rename_repo).strip() == "R100\told.txt\tnew.txt"
    assert _index_content(rename_repo, "new.txt") == "line 1\nline 2\n"
    assert _uncached_name_status(rename_repo).strip() == "M\tnew.txt"


def test_include_rename_then_mode_change_targets_destination(rename_repo):
    """The mode action following a rename must operate on the renamed path."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    assert isinstance(load_selected_change(), RenameChange)

    command_include(quiet=True)
    selected_change = load_selected_change()
    assert selected_change == FileModeChange(
        "new.txt",
        "100644",
        "100755",
    )

    command_include(quiet=True)

    index_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "new.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert index_entry.startswith("100755 ")


def test_include_mode_after_skipped_rename_targets_source_index_path(rename_repo):
    """A mode-only include must work while the paired rename stays unstaged."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    command_skip(quiet=True)
    selected_change = load_selected_change()
    assert selected_change == FileModeChange(
        "new.txt",
        "100644",
        "100755",
        index_path="old.txt",
    )
    original_index_entries = subprocess.run(
        ["git", "ls-files", "--stage", "--", "old.txt", "new.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout

    command_include(quiet=True)

    index_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "old.txt", "new.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    old_index_entry = next(
        line for line in index_entry.splitlines() if line.endswith("\told.txt")
    )
    assert old_index_entry.startswith("100755 ")

    command_undo(force=True)
    restored_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "old.txt", "new.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert restored_entry == original_index_entries


def test_stage_mode_after_skipped_rename_preserves_source_index_flags(
    rename_repo,
):
    """A paired mode update must retain intent on the source index entry."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    command_skip(quiet=True)
    selected_change = load_selected_change()
    assert isinstance(selected_change, FileModeChange)
    subprocess.run(
        ["git", "update-index", "--force-remove", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
    )
    alternate_worktree = rename_repo.parent / "alternate-worktree"
    alternate_worktree.mkdir()
    (alternate_worktree / "old.txt").write_text("intent placeholder\n")
    subprocess.run(
        [
            "git",
            f"--work-tree={alternate_worktree}",
            "add",
            "-N",
            "--",
            "old.txt",
        ],
        check=True,
        cwd=rename_repo,
        capture_output=True,
    )
    assert "flags: 20004000" in subprocess.run(
        ["git", "ls-files", "--debug", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout

    stage_file_mode_change(selected_change)

    source_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    source_debug = subprocess.run(
        ["git", "ls-files", "--debug", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert source_entry.startswith("100755 ")
    assert "flags: 20004000" in source_debug


def test_stage_mode_after_skipped_rename_preserves_assume_unchanged(
    rename_repo,
):
    """A paired mode update must retain ordinary source index flags."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    command_skip(quiet=True)
    selected_change = load_selected_change()
    assert isinstance(selected_change, FileModeChange)
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
    )

    stage_file_mode_change(selected_change)

    source_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    source_flags = subprocess.run(
        ["git", "ls-files", "-v", "--", "old.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert source_entry.startswith("100755 ")
    assert source_flags == "h old.txt\n"


def test_include_rename_source_file_also_stages_paired_mode(rename_repo):
    """The source spelling of a whole-file rename still covers its mode action."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    command_include_file("old.txt", quiet=True, auto_advance=False)

    index_entry = subprocess.run(
        ["git", "ls-files", "--stage", "--", "new.txt"],
        check=True,
        cwd=rename_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert index_entry.startswith("100755 ")


def test_mode_after_unstaged_rename_cannot_be_saved_as_standalone_batch(rename_repo):
    """Mode-only batch metadata cannot faithfully carry a pending rename."""
    _rename_without_staging(rename_repo)
    (rename_repo / "new.txt").chmod(0o755)

    command_start(quiet=True)
    command_skip(quiet=True)
    assert isinstance(load_selected_change(), FileModeChange)

    with pytest.raises(CommandError, match="rename from 'old.txt' is unstaged"):
        command_include_to_batch("mode-only", quiet=True)

    assert not batch_exists("mode-only")


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
