"""Tests for page-aware file review state and safety."""

from __future__ import annotations

from dataclasses import replace
import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim, ReplacementUnit
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.discard import command_discard_file, command_discard_file_as, command_discard_line, command_discard_line_as_to_batch, command_discard_to_batch
from git_stage_batch.commands.include import command_include, command_include_file, command_include_file_as, command_include_line, command_include_line_as
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.reset import command_reset_from_batch
from git_stage_batch.commands.show_from import command_show_from_batch
import git_stage_batch.commands.show_from as show_from_module
from git_stage_batch.commands.show import command_show, command_show_file_list
import git_stage_batch.commands.show as show_module
from git_stage_batch.commands.skip import command_skip, command_skip_file, command_skip_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.stop import command_stop
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.suggest_fixup import command_suggest_fixup
import git_stage_batch.data.hunk_tracking as hunk_tracking_module
from git_stage_batch.data.file_review_state import (
    FileReviewAction,
    ReviewSource,
    read_last_file_review_state,
    shown_complete_review_selection_groups,
)
from git_stage_batch.data.hunk_tracking import (
    SelectedChangeKind,
    get_selected_change_file_path,
    read_selected_change_kind,
)
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.core.line_selection import format_line_ids
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange, RenderedBatchDisplay, ReviewActionGroup
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.output.file_review import build_file_review_model, make_file_review_state, print_file_review
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_line_changes_json_file_path,
    get_selected_change_clear_reason_file_path,
)


@pytest.fixture
def paged_file_repo(tmp_path, monkeypatch):
    """Create a repo with three separated file changes."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    original = [f"line {number}\n" for number in range(1, 31)]
    (tmp_path / "file.txt").write_text("".join(original))
    subprocess.run(["git", "add", "file.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    changed = original[:]
    changed[1] = "line 2 changed\n"
    changed[11] = "line 12 changed\n"
    changed[21] = "line 22 changed\n"
    (tmp_path / "file.txt").write_text("".join(changed))

    ensure_state_directory_exists()
    initialize_abort_state()
    return tmp_path


@pytest.fixture
def paged_batch_repo(paged_file_repo):
    """Create a batch with the paged file changes."""
    command_start()
    command_include_to_batch("cleanup", file="file.txt", quiet=True)
    return paged_file_repo


def _force_one_change_per_page(monkeypatch):
    from git_stage_batch.output import file_review

    monkeypatch.setattr(file_review, "_body_budget", lambda: 1)


def _add_second_changed_file(repo):
    other_file = repo / "other.txt"
    other_file.write_text("other 1\nother 2\n")
    subprocess.run(["git", "add", "other.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add other"], check=True, capture_output=True)
    other_file.write_text("other 1\nother changed\n")


def _create_multi_file_batch(repo):
    _add_second_changed_file(repo)
    command_start()
    command_include_to_batch("cleanup", file="file.txt", quiet=True)
    command_include_to_batch("cleanup", file="other.txt", quiet=True)


def test_show_file_page_range_renders_pages_and_persists_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show(file="file.txt", page="2-3")

    captured = capsys.readouterr()
    assert "── page 2/3" in captured.out
    assert "── page 3/3" in captured.out
    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (2, 3)
    assert state.page_spec == "2-3"
    assert state.entire_file_shown is False


def test_show_file_page_all_sets_entire_file_shown(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show(file="file.txt", page="all")

    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (1, 2, 3)
    assert state.page_spec == "all"
    assert state.entire_file_shown is True


def test_show_file_numeric_all_pages_normalizes_to_all(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show(file="file.txt", page="1,3,2")

    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (1, 2, 3)
    assert state.page_spec == "all"
    assert state.entire_file_shown is True


def test_show_file_duplicate_pages_normalize_to_compact_set(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show(file="file.txt", page="3,2,2")

    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (2, 3)
    assert state.page_spec == "2-3"


def test_show_file_rejects_invalid_page_specs(paged_file_repo, monkeypatch):
    _force_one_change_per_page(monkeypatch)

    with pytest.raises(CommandError, match="Available pages: 1-3"):
        command_show(file="file.txt", page="99")
    with pytest.raises(CommandError, match="cannot be combined"):
        command_show(file="file.txt", page="all,3")
    with pytest.raises(CommandError, match="cannot be combined"):
        command_show(file="file.txt", page="1, all")
    with pytest.raises(CommandError, match="Invalid page selection"):
        command_show(file="file.txt", page="1-all")
    with pytest.raises(CommandError, match="Invalid page selection"):
        command_show(file="file.txt", page="all-3")
    with pytest.raises(CommandError, match="Invalid page selection"):
        command_show(file="file.txt", page="small")
    with pytest.raises(CommandError, match="empty"):
        command_show(file="file.txt", page="")
    with pytest.raises(CommandError, match="empty"):
        command_show(file="file.txt", page="1,,2")


def test_show_file_invalid_page_preserves_previous_review_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    with pytest.raises(CommandError, match="Available pages: 1-3"):
        command_show(file="file.txt", page="99")

    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (1,)


def test_show_file_invalid_page_does_not_select_file_review(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_start()
    capsys.readouterr()
    assert read_selected_change_kind() == SelectedChangeKind.HUNK
    assert get_selected_change_file_path() == "file.txt"

    with pytest.raises(CommandError, match="Available pages: 1-3"):
        command_show(file="file.txt", page="99")

    assert read_selected_change_kind() == SelectedChangeKind.HUNK
    assert get_selected_change_file_path() == "file.txt"
    assert read_last_file_review_state() is None

def test_show_from_batch_file_defaults_to_page_review_and_persists_state(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show_from_batch("cleanup", file="file.txt")

    captured = capsys.readouterr()
    assert "Changes: batch cleanup" in captured.out
    assert "Showing page 1 of 3" in captured.out
    state = read_last_file_review_state()
    assert state is not None
    assert state.source == "batch"
    assert state.shown_pages == (1,)
