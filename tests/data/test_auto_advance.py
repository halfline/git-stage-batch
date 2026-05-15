"""Tests for the automatic hunk selection preference."""

import subprocess

import pytest

from git_stage_batch.commands.again import command_again
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.auto_advance import (
    read_auto_advance_default,
    write_auto_advance_default,
)
from git_stage_batch.data.hunk_tracking import (
    refuse_bare_action_after_auto_advance_disabled,
    select_next_change_after_action,
    selected_change_was_cleared_by_auto_advance_disabled,
)
from git_stage_batch.exceptions import CommandError
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


def test_start_initializes_auto_advance_default(temp_git_repo):
    readme = temp_git_repo / "README.md"
    readme.write_text("# Test\nChanged\n")

    command_start(quiet=True, auto_advance=False)

    assert read_auto_advance_default() is False


def test_again_updates_auto_advance_default(temp_git_repo):
    readme = temp_git_repo / "README.md"
    readme.write_text("# Test\nChanged\n")
    command_start(quiet=True, auto_advance=False)

    command_again(quiet=True, auto_advance=True)

    assert read_auto_advance_default() is True


def test_again_preserves_auto_advance_default_without_override(temp_git_repo):
    readme = temp_git_repo / "README.md"
    readme.write_text("# Test\nChanged\n")
    command_start(quiet=True, auto_advance=False)

    command_again(quiet=True)

    assert read_auto_advance_default() is False


def test_disabled_auto_advance_records_empty_selection(temp_git_repo):
    assert select_next_change_after_action(auto_advance=False) is False
    assert selected_change_was_cleared_by_auto_advance_disabled() is True

    with pytest.raises(CommandError, match="automatic advancement is disabled"):
        refuse_bare_action_after_auto_advance_disabled("include")
