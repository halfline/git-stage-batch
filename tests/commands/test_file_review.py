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
from git_stage_batch.output.colors import Colors
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


def test_non_selectable_live_preview_preserves_partial_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _add_second_changed_file(paged_file_repo)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.entire_file_shown is False

    command_show(file="other.txt", selectable=False)
    capsys.readouterr()

    preserved_state = read_last_file_review_state()
    assert preserved_state is not None
    assert preserved_state.file_path == "file.txt"
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include(quiet=True)


def test_non_selectable_batch_preview_preserves_partial_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.entire_file_shown is False

    command_show_from_batch("cleanup", file="other.txt", selectable=False)
    capsys.readouterr()

    preserved_state = read_last_file_review_state()
    assert preserved_state is not None
    assert preserved_state.file_path == "file.txt"
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include_from_batch("cleanup")


def test_show_file_and_file_list_tolerate_binary_changes(paged_file_repo, capsys):
    binary_path = paged_file_repo / "asset.bin"
    binary_path.write_bytes(b"\x00\x01\x02")
    subprocess.run(["git", "add", "asset.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add binary"], check=True, capture_output=True)
    binary_path.write_bytes(b"\x00\x03\x04")

    command_show(file="asset.bin")

    captured = capsys.readouterr()
    assert "asset.bin :: Binary file modified" in captured.out
    assert read_selected_change_kind() == SelectedChangeKind.BINARY

    command_show_file_list(["file.txt", "asset.bin"])

    captured = capsys.readouterr()
    assert "asset.bin" in captured.out
    assert "binary modified" in captured.out


def test_show_text_file_after_binary_file_drops_stale_binary_selection(paged_file_repo, capsys):
    binary_path = paged_file_repo / "asset.bin"
    binary_path.write_bytes(b"\x00\x01\x02")
    subprocess.run(["git", "add", "asset.bin"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add binary"], check=True, capture_output=True)
    binary_path.write_bytes(b"\x00\x03\x04")

    command_show(file="asset.bin")
    capsys.readouterr()
    assert read_selected_change_kind() == SelectedChangeKind.BINARY
    assert get_selected_change_file_path() == "asset.bin"

    command_show(file="file.txt", porcelain=True)

    assert read_selected_change_kind() == SelectedChangeKind.FILE
    assert get_selected_change_file_path() == "file.txt"

    command_include(quiet=True)

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == ["file.txt"]


def test_show_from_batch_invalid_page_does_not_select_batch_file(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    capsys.readouterr()
    before_kind = read_selected_change_kind()
    before_file = get_selected_change_file_path()

    with pytest.raises(CommandError, match="Available pages: 1-3"):
        command_show_from_batch("cleanup", file="file.txt", page="99")

    assert read_selected_change_kind() == before_kind
    assert get_selected_change_file_path() == before_file
    assert read_last_file_review_state() is None


def test_show_from_batch_invalid_line_does_not_select_batch_file(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    before_kind = read_selected_change_kind()
    before_file = get_selected_change_file_path()
    before_state = read_last_file_review_state()
    assert before_kind == SelectedChangeKind.FILE
    assert before_file == "file.txt"
    assert before_state is not None

    with pytest.raises(CommandError, match="Line ID 999 is not available"):
        command_show_from_batch("cleanup", file="file.txt", line_ids="999")

    assert read_selected_change_kind() == before_kind
    assert get_selected_change_file_path() == before_file
    assert read_last_file_review_state() == before_state


def test_show_file_porcelain_clears_previous_review_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    command_show(file="file.txt", porcelain=True)

    assert read_last_file_review_state() is None


def test_bare_include_refuses_after_partial_file_review(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_include()

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert "git-stage-batch include --file file.txt" in exc_info.value.message


def test_show_unchanged_file_preserves_partial_file_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    unchanged_file = paged_file_repo / "unchanged.txt"
    unchanged_file.write_text("same\n")
    subprocess.run(["git", "add", "unchanged.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add unchanged"], check=True, capture_output=True)

    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.entire_file_shown is False

    command_show(file="unchanged.txt")
    capsys.readouterr()

    preserved_state = read_last_file_review_state()
    assert preserved_state is not None
    assert preserved_state.file_path == "file.txt"
    assert preserved_state.shown_pages == (1,)
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include(quiet=True)


def test_show_file_list_without_entries_preserves_partial_file_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    unchanged_file = paged_file_repo / "unchanged.txt"
    unchanged_file.write_text("same\n")
    subprocess.run(["git", "add", "unchanged.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add unchanged"], check=True, capture_output=True)

    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None

    command_show_file_list(["unchanged.txt"])
    capsys.readouterr()

    assert read_last_file_review_state() == state
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include(quiet=True)


def test_show_from_empty_batch_preserves_partial_batch_review_guard(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None

    create_batch("empty")
    command_show_from_batch("empty")
    capsys.readouterr()

    assert read_last_file_review_state() == state
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include_from_batch("cleanup")


def test_plain_show_without_unblocked_hunk_preserves_partial_file_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None

    command_skip_file("file.txt", quiet=True, advance=False)
    capsys.readouterr()
    command_show()
    capsys.readouterr()

    assert read_last_file_review_state() == state
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include(quiet=True)


def test_plain_show_with_only_batch_filtered_hunks_preserves_partial_file_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None

    monkeypatch.setattr(show_module, "apply_line_level_batch_filter_to_cached_hunk", lambda: True)

    command_show()
    capsys.readouterr()

    assert read_last_file_review_state() == state
    assert read_selected_change_kind() == SelectedChangeKind.FILE
    assert get_selected_change_file_path() == "file.txt"
    with pytest.raises(CommandError, match="Only pages 1 of 3"):
        command_include(quiet=True)


def test_bare_include_to_batch_refuses_after_partial_file_review(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="2")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_include_to_batch("later")

    assert "Only pages 2 of 3 of file.txt were shown" in exc_info.value.message
    assert "git-stage-batch show --file file.txt --page all" in exc_info.value.message


@pytest.mark.parametrize(
    "to_batch_action",
    [
        lambda: command_include_to_batch("later", file="", quiet=True),
        lambda: command_discard_to_batch("later", file="", quiet=True),
    ],
)
def test_default_to_batch_file_actions_refuse_after_partial_file_review(
    paged_file_repo,
    monkeypatch,
    capsys,
    to_batch_action,
):
    _force_one_change_per_page(monkeypatch)
    original_content = (paged_file_repo / "file.txt").read_text()
    command_show(file="file.txt", page="1")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        to_batch_action()

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert (paged_file_repo / "file.txt").read_text() == original_content


def test_default_discard_line_as_to_batch_keeps_partial_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    command_discard_line_as_to_batch(
        "later",
        line_spec,
        "replacement\n",
        file="",
        quiet=True,
    )
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_discard_line_as_to_batch(
            "later",
            unshown_spec,
            "replacement\n",
            file="",
            quiet=True,
        )

    line_changes = load_line_changes_from_state()
    assert line_changes is not None
    visible_changed_texts = [
        line.display_text()
        for line in line_changes.lines
        if line.id is not None
    ]
    assert "line 2" not in visible_changed_texts
    assert "line 2 changed" not in visible_changed_texts


def test_include_to_batch_refuses_after_navigational_file_list(paged_file_repo, capsys):
    _add_second_changed_file(paged_file_repo)

    command_show_file_list(["file.txt", "other.txt"])
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_include_to_batch("later", quiet=True)


@pytest.mark.parametrize(
    "line_command",
    [command_include_line, command_skip_line, command_discard_line],
)
def test_pathless_line_actions_refuse_after_navigational_file_list(
    paged_file_repo,
    capsys,
    line_command,
):
    _add_second_changed_file(paged_file_repo)

    command_show_file_list(["file.txt", "other.txt"])
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        line_command("1")


def test_file_list_marker_survives_explicit_file_include_line(
    paged_file_repo,
    capsys,
):
    _add_second_changed_file(paged_file_repo)

    command_show_file_list(["file.txt", "other.txt"])
    capsys.readouterr()

    command_include_line("1", file="other.txt")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_include_to_batch("later", quiet=True)


def test_bare_include_to_batch_after_full_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _add_second_changed_file(paged_file_repo)
    command_show(file="other.txt", page="all")
    capsys.readouterr()

    command_include_to_batch("reviewed", quiet=True)

    metadata = read_batch_metadata("reviewed")
    assert list(metadata.get("files", {}).keys()) == ["other.txt"]


def test_pathless_include_to_batch_line_filters_file_review_selection(
    tmp_path,
    monkeypatch,
    capsys,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, capture_output=True)

    test_file = repo / "file.txt"
    test_file.write_text("a\nb\n")
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)
    test_file.write_text("a\nX\nb\nY\n")

    ensure_state_directory_exists()
    initialize_abort_state()
    command_show(file="file.txt", page="all")
    capsys.readouterr()

    command_include_to_batch("first", line_ids="1", quiet=True)
    capsys.readouterr()

    filtered = load_line_changes_from_state()
    assert filtered is not None
    assert [line.display_text() for line in filtered.lines if line.id is not None] == ["Y"]

    command_include_to_batch("second", line_ids="1", quiet=True)

    first_metadata = read_batch_metadata("first")
    second_metadata = read_batch_metadata("second")
    assert (
        first_metadata["files"]["file.txt"]["presence_claims"] !=
        second_metadata["files"]["file.txt"]["presence_claims"]
    )
    assert read_selected_change_kind() is None


def test_bare_discard_to_batch_refuses_after_partial_file_review(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="2")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_discard_to_batch("later")

    assert "Only pages 2 of 3 of file.txt were shown" in exc_info.value.message
    assert "line 12 changed" in (paged_file_repo / "file.txt").read_text()


def test_discard_to_batch_refuses_after_navigational_file_list(paged_file_repo, capsys):
    _add_second_changed_file(paged_file_repo)
    original_file = (paged_file_repo / "file.txt").read_text()
    original_other = (paged_file_repo / "other.txt").read_text()

    command_show_file_list(["file.txt", "other.txt"])
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_discard_to_batch("later", quiet=True)

    assert (paged_file_repo / "file.txt").read_text() == original_file
    assert (paged_file_repo / "other.txt").read_text() == original_other


def test_include_from_batch_refuses_after_navigational_batch_file_list(paged_file_repo, capsys):
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()

    command_show_from_batch("cleanup")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_include_from_batch("cleanup")

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    assert result.returncode == 0


def test_live_file_list_does_not_block_unrelated_batch_action(paged_file_repo, capsys):
    command_start()
    command_include_to_batch("cleanup", file="file.txt", quiet=True)
    _add_second_changed_file(paged_file_repo)
    capsys.readouterr()

    command_show_file_list(["file.txt", "other.txt"])
    capsys.readouterr()

    command_include_from_batch("cleanup")

    captured = capsys.readouterr()
    assert "Staged changes from batch 'cleanup'" in captured.err
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == ["file.txt"]


def test_legacy_file_list_marker_does_not_block_batch_action(paged_file_repo, capsys):
    command_start()
    command_include_to_batch("cleanup", file="file.txt", quiet=True)
    capsys.readouterr()
    get_selected_change_clear_reason_file_path().write_text("file-list")

    command_include_from_batch("cleanup")

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == ["file.txt"]


def test_discard_from_batch_refuses_after_navigational_batch_file_list(paged_file_repo, capsys):
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    original_file = (paged_file_repo / "file.txt").read_text()
    original_other = (paged_file_repo / "other.txt").read_text()

    command_show_from_batch("cleanup")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_discard_from_batch("cleanup")

    assert (paged_file_repo / "file.txt").read_text() == original_file
    assert (paged_file_repo / "other.txt").read_text() == original_other


def test_apply_from_batch_refuses_after_navigational_batch_file_list(paged_file_repo, capsys):
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    original_file = (paged_file_repo / "file.txt").read_text()
    original_other = (paged_file_repo / "other.txt").read_text()

    command_show_from_batch("cleanup")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_apply_from_batch("cleanup")

    assert (paged_file_repo / "file.txt").read_text() == original_file
    assert (paged_file_repo / "other.txt").read_text() == original_other


def test_reset_from_batch_refuses_after_navigational_batch_file_list(paged_file_repo, capsys):
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()

    command_show_from_batch("cleanup")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        command_reset_from_batch("cleanup")

    metadata = read_batch_metadata("cleanup")
    assert set(metadata.get("files", {})) == {"file.txt", "other.txt"}


def test_bare_discard_to_batch_after_full_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _add_second_changed_file(paged_file_repo)
    command_show(file="other.txt", page="all")
    capsys.readouterr()

    command_discard_to_batch("reviewed", quiet=True)

    metadata = read_batch_metadata("reviewed")
    assert list(metadata.get("files", {}).keys()) == ["other.txt"]
    assert (paged_file_repo / "other.txt").read_text() == "other 1\nother 2\n"
    assert "line 2 changed" in (paged_file_repo / "file.txt").read_text()


def test_bare_include_allowed_after_entire_file_review(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="all")
    capsys.readouterr()

    command_include()

    captured = capsys.readouterr()
    assert "Staged" in captured.err


def test_bare_include_after_full_file_review_refuses_when_file_changed(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="all")
    capsys.readouterr()

    file_path = paged_file_repo / "file.txt"
    file_path.write_text(file_path.read_text() + "unreviewed change\n")

    with pytest.raises(CommandError, match="no longer matches"):
        command_include()


def test_pathless_include_line_accepts_complete_shown_change_and_keeps_partial_review_guard(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_include_line(line_spec)

    captured = capsys.readouterr()
    assert f"Included line(s): {line_spec}" in captured.err
    assert read_last_file_review_state() is not None


def test_pathless_include_line_as_keeps_partial_review_guard_for_bare_action(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_include_line_as(line_spec, "replacement\n")
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_include(quiet=True)


def test_discard_line_as_to_batch_keeps_partial_review_guard_for_bare_action(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_discard_line_as_to_batch(
        "later",
        line_spec,
        "replacement\n",
        file="",
        quiet=True,
    )
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_include(quiet=True)


def test_show_file_resets_processed_skip_ids_before_review_line_action(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    (tmp_path / "a.txt").write_text("a1\na2\na3\n")
    (tmp_path / "b.txt").write_text("b1\nb2\nb3\nb4\nb5\n")
    subprocess.run(["git", "add", "a.txt", "b.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    (tmp_path / "a.txt").write_text("a1 changed\na2\na3 changed\n")
    (tmp_path / "b.txt").write_text("b1 changed\nb2\nb3 changed\nb4\nb5\n")

    command_start(quiet=True)
    command_skip_line("1")
    capsys.readouterr()

    command_show(file="b.txt", page="all")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    second_change_spec = format_line_ids(list(state.selections[1].display_ids))

    command_skip_line(second_change_spec)

    line_changes = load_line_changes_from_state()
    assert line_changes is not None
    visible_changed_texts = [
        line.display_text()
        for line in line_changes.lines
        if line.id is not None
    ]
    assert "b1" in visible_changed_texts
    assert "b1 changed" in visible_changed_texts
    assert "b3" not in visible_changed_texts
    assert "b3 changed" not in visible_changed_texts


def test_file_review_footer_suggests_pathless_line_commands(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show(file="file.txt", page="1")

    captured = capsys.readouterr()
    assert "git-stage-batch include --line" in captured.out
    assert "git-stage-batch include --file file.txt --line" not in captured.out


def test_pathless_include_line_rejects_partial_replacement_selection(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    captured = capsys.readouterr()
    assert "Change 1/3   lines 1–2   2-line group" in captured.out

    state = read_last_file_review_state()
    assert state is not None
    partial_id = str(state.selections[0].display_ids[0])

    with pytest.raises(CommandError, match="only partly selects"):
        command_include_line(partial_id)


def test_pathless_include_line_rejects_unshown_change(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    with pytest.raises(CommandError, match="not valid from the current file review"):
        command_include_line(unshown_spec)


def test_partial_review_line_action_keeps_stale_guard_for_followup_bare_action(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    shown_spec = format_line_ids(list(state.selections[0].display_ids))

    command_include_line(shown_spec)
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_include()


def test_partial_review_line_action_keeps_stale_guard_for_followup_line_action(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    shown_spec = format_line_ids(list(state.selections[0].display_ids))
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    command_skip_line(shown_spec)
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_skip_line(unshown_spec)


@pytest.mark.parametrize(
    "to_batch_command",
    [command_include_to_batch, command_discard_to_batch],
)
def test_partial_review_to_batch_line_action_keeps_stale_guard_for_followup_line_action(
    paged_file_repo,
    monkeypatch,
    capsys,
    to_batch_command,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    shown_spec = format_line_ids(list(state.selections[0].display_ids))
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    to_batch_command("cleanup", line_ids=shown_spec)
    capsys.readouterr()

    assert read_last_file_review_state() is not None
    with pytest.raises(CommandError, match="no longer matches"):
        command_include_to_batch("cleanup", line_ids=unshown_spec)


@pytest.mark.parametrize(
    "to_batch_command",
    [command_include_to_batch, command_discard_to_batch],
)
def test_implicit_to_batch_line_action_clears_full_review_state(
    paged_file_repo,
    capsys,
    to_batch_command,
):
    command_show(file="file.txt", page="all")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    shown_spec = format_line_ids(list(state.selections[0].display_ids))

    to_batch_command("cleanup", line_ids=shown_spec, file="")

    assert read_last_file_review_state() is None


@pytest.mark.parametrize(
    "line_command",
    [command_include_line, command_skip_line, command_discard_line],
)
def test_omitted_file_line_actions_reject_unshown_change(
    paged_file_repo,
    monkeypatch,
    capsys,
    line_command,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    with pytest.raises(CommandError, match="not valid from the current file review"):
        line_command(unshown_spec, file="")


def test_explicit_include_file_line_from_review_clears_review_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_include_line(line_spec, file="file.txt")

    assert read_last_file_review_state() is None


def test_explicit_skip_file_line_from_review_clears_review_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_skip_line(line_spec, file="file.txt")

    assert read_last_file_review_state() is None


def test_explicit_discard_file_line_from_review_clears_review_state(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_discard_line(line_spec, file="file.txt")

    assert read_last_file_review_state() is None


@pytest.mark.parametrize(
    "replace_file",
    [
        lambda file_path: command_include_file_as("replacement\n", file=file_path),
        lambda file_path: command_discard_file_as("replacement\n", file=file_path),
    ],
)
def test_explicit_file_as_from_review_clears_matching_review_state(
    paged_file_repo,
    monkeypatch,
    capsys,
    replace_file,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="all")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    replace_file("file.txt")

    assert read_last_file_review_state() is None


def test_explicit_discard_to_batch_line_as_from_review_clears_matching_review_state(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show(file="file.txt", page="all")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_discard_line_as_to_batch(
        "cleanup",
        line_spec,
        "replacement\n",
        file="file.txt",
        quiet=True,
    )

    assert read_last_file_review_state() is None


def test_explicit_skip_line_on_different_file_clears_previous_review_state(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _add_second_changed_file(paged_file_repo)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    command_skip_line("1", file="other.txt")

    assert read_last_file_review_state() is None


def test_explicit_discard_line_on_different_file_clears_previous_review_state(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _add_second_changed_file(paged_file_repo)
    command_show(file="file.txt", page="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    command_discard_line("1", file="other.txt")

    assert read_last_file_review_state() is None


def test_show_file_opens_near_selected_hunk_by_line_span(paged_file_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show()
    capsys.readouterr()
    command_skip()
    capsys.readouterr()

    command_show(file="file.txt")

    captured = capsys.readouterr()
    assert "file.txt  ·  file vs HEAD  ·  page 2/3" in captured.out
    assert "Showing the area around the change you were viewing." in captured.out
    state = read_last_file_review_state()
    assert state is not None
    assert state.shown_pages == (2,)


def test_show_from_batch_file_page_uses_gutter_ids_in_output_and_state(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show_from_batch("cleanup", file="file.txt", page="1")

    captured = capsys.readouterr()
    assert "file.txt  ·  cleanup  ·  page 1/3  ·  change 1/3" in captured.out
    assert "Note: Auto-created" in captured.out
    assert "# Auto-created" not in captured.err
    assert "file.txt  ·  page 1/3  ·  change 1/3" in captured.out
    assert "include  git-stage-batch include --from cleanup --line" in captured.out
    assert "git-stage-batch include --from cleanup --line" in captured.out
    assert "all      git-stage-batch show --from cleanup --file file.txt --page all" in captured.out
    assert "git-stage-batch show --from cleanup --file file.txt --page all" in captured.out
    assert "[#1]" in captured.out
    state = read_last_file_review_state()
    assert state is not None
    assert state.source == "batch"
    assert state.batch_name == "cleanup"
    assert state.selected_change_kind == "batch-file"
    assert state.selections[0].display_ids
    assert state.selections[0].selection_ids

    command_show_from_batch("cleanup", file="file.txt", page="all")

    captured = capsys.readouterr()
    assert "git-stage-batch include --from cleanup --line" in captured.out
    assert "git-stage-batch discard --from cleanup --line" in captured.out


def test_pathless_include_from_batch_accepts_same_batch_review_selection(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    command_include_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert "Staged selected lines from batch 'cleanup'" in captured.err
    assert read_last_file_review_state() is not None

    with pytest.raises(CommandError, match="not valid from the current file review"):
        command_include_from_batch("cleanup", line_ids=unshown_spec)


def test_pathless_include_from_batch_uses_reviewed_file_in_multi_file_batch(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_include_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert "Staged selected lines from batch 'cleanup'" in captured.err
    assert read_last_file_review_state() is not None


def test_pathless_live_line_after_full_batch_review_refuses_with_batch_help(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_include_line("1")

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert "git-stage-batch include --from cleanup --file file.txt --line 1" in exc_info.value.message


@pytest.mark.parametrize(
    "action",
    [
        lambda: command_include_line("1", file=""),
        lambda: command_skip_line("1", file=""),
        lambda: command_discard_line("1", file=""),
    ],
)
def test_live_default_file_line_actions_after_batch_review_refuse(
    paged_batch_repo,
    monkeypatch,
    capsys,
    action,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        action()

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message


@pytest.mark.parametrize(
    "file_command",
    [command_include_file, command_skip_file, command_discard_file],
)
def test_live_default_file_actions_after_batch_review_refuse(
    paged_batch_repo,
    monkeypatch,
    capsys,
    file_command,
):
    _force_one_change_per_page(monkeypatch)
    original_content = (paged_batch_repo / "file.txt").read_text()
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        file_command("")

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert (paged_batch_repo / "file.txt").read_text() == original_content


@pytest.mark.parametrize(
    "as_command",
    [
        lambda: command_include_file_as("replacement\n", file=""),
        lambda: command_discard_file_as("replacement\n", file=""),
    ],
)
def test_live_default_file_as_actions_after_batch_review_refuse(
    paged_batch_repo,
    monkeypatch,
    capsys,
    as_command,
):
    _force_one_change_per_page(monkeypatch)
    original_content = (paged_batch_repo / "file.txt").read_text()
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        as_command()

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert (paged_batch_repo / "file.txt").read_text() == original_content


def test_pathless_live_line_after_filtered_batch_show_refuses_before_stale_snapshot(
    paged_batch_repo,
    capsys,
):
    command_show_from_batch("cleanup", file="file.txt", line_ids="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    with pytest.raises(CommandError) as exc_info:
        command_include_line("1")

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert "Cached hunk is stale" not in exc_info.value.message


def test_bare_include_from_batch_after_filtered_file_show_does_not_widen_to_whole_batch(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()

    command_show_from_batch("cleanup", file="file.txt", line_ids="1")
    capsys.readouterr()

    state = read_last_file_review_state()
    assert state is not None
    assert state.file_path == "file.txt"
    assert state.entire_file_shown is False

    with pytest.raises(CommandError) as exc_info:
        command_include_from_batch("cleanup")

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    staged_files = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged_files == []


def test_filtered_batch_show_state_only_allows_displayed_selection(
    paged_batch_repo,
    capsys,
):
    command_show_from_batch("cleanup", file="file.txt", line_ids="1")
    capsys.readouterr()

    with pytest.raises(CommandError, match="not valid from the current file review"):
        command_include_from_batch("cleanup", line_ids="2")

    staged_files = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged_files == []


@pytest.mark.parametrize(
    "file_command",
    [command_include_file, command_skip_file, command_discard_file],
)
def test_default_file_actions_refuse_after_partial_live_file_review(
    paged_file_repo,
    monkeypatch,
    capsys,
    file_command,
):
    _force_one_change_per_page(monkeypatch)
    original_content = (paged_file_repo / "file.txt").read_text()
    command_show(file="file.txt", page="1")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        file_command("")

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert (paged_file_repo / "file.txt").read_text() == original_content


@pytest.mark.parametrize(
    "as_command",
    [
        lambda: command_include_file_as("replacement\n", file=""),
        lambda: command_discard_file_as("replacement\n", file=""),
    ],
)
def test_default_file_as_actions_refuse_after_partial_live_file_review(
    paged_file_repo,
    monkeypatch,
    capsys,
    as_command,
):
    _force_one_change_per_page(monkeypatch)
    original_content = (paged_file_repo / "file.txt").read_text()
    command_show(file="file.txt", page="1")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        as_command()

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert (paged_file_repo / "file.txt").read_text() == original_content


def test_live_hunk_only_command_after_batch_review_refuses_without_clearing_state(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_suggest_fixup()

    assert "came from a batch, not a live hunk" in exc_info.value.message
    assert read_selected_change_kind() == SelectedChangeKind.BATCH_FILE
    assert read_last_file_review_state() is not None


def test_bare_include_to_batch_after_full_batch_review_refuses_with_batch_help(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_include_to_batch("later")

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert "Batch reviews do not support this action" in exc_info.value.message


def test_bare_discard_to_batch_after_full_batch_review_refuses_with_batch_help(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_discard_to_batch("later")

    assert "came from batch 'cleanup', not the live working tree" in exc_info.value.message
    assert "Batch reviews do not support this action" in exc_info.value.message


def test_explicit_empty_file_discard_to_batch_after_batch_review_uses_selected_file(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    command_discard_to_batch("later", file="", quiet=True)

    metadata = read_batch_metadata("later")
    assert list(metadata.get("files", {}).keys()) == ["file.txt"]


def test_bare_include_from_batch_refuses_after_partial_batch_review(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()

    with pytest.raises(CommandError) as exc_info:
        command_include_from_batch("cleanup")

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert "git-stage-batch include --from cleanup --file file.txt" in exc_info.value.message
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    assert result.returncode == 0


def test_bare_discard_from_batch_refuses_after_partial_batch_review(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    original_content = (paged_batch_repo / "file.txt").read_text()

    with pytest.raises(CommandError) as exc_info:
        command_discard_from_batch("cleanup")

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert "git-stage-batch discard --from cleanup --file file.txt" in exc_info.value.message
    assert (paged_batch_repo / "file.txt").read_text() == original_content


@pytest.mark.parametrize(
    "batch_file_action",
    [
        lambda: command_include_from_batch("cleanup", file=""),
        lambda: command_discard_from_batch("cleanup", file=""),
        lambda: command_apply_from_batch("cleanup", file=""),
        lambda: command_reset_from_batch("cleanup", file=""),
    ],
)
def test_default_batch_file_actions_refuse_after_partial_batch_review(
    paged_batch_repo,
    monkeypatch,
    capsys,
    batch_file_action,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    original_content = (paged_batch_repo / "file.txt").read_text()

    with pytest.raises(CommandError) as exc_info:
        batch_file_action()

    assert "Only pages 1 of 3 of file.txt were shown" in exc_info.value.message
    assert (paged_batch_repo / "file.txt").read_text() == original_content
    assert "file.txt" in read_batch_metadata("cleanup").get("files", {})


@pytest.mark.parametrize(
    "batch_file_action",
    [
        lambda: command_include_from_batch("cleanup", file=""),
        lambda: command_discard_from_batch("cleanup", file=""),
        lambda: command_apply_from_batch("cleanup", file=""),
        lambda: command_reset_from_batch("cleanup", file=""),
    ],
)
def test_default_batch_file_actions_refuse_after_batch_file_list(
    paged_file_repo,
    capsys,
    batch_file_action,
):
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup")
    capsys.readouterr()

    with pytest.raises(CommandError, match="last command only showed files"):
        batch_file_action()

    metadata = read_batch_metadata("cleanup")
    assert set(metadata.get("files", {})) == {"file.txt", "other.txt"}


def test_bare_include_from_batch_after_full_single_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    command_include_from_batch("cleanup")

    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == ["file.txt"]


def test_bare_discard_from_batch_after_full_single_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    command_discard_from_batch("cleanup")

    assert "line 2 changed" not in (paged_file_repo / "file.txt").read_text()
    assert (paged_file_repo / "other.txt").read_text() == "other 1\nother changed\n"


def test_bare_apply_from_batch_after_full_single_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    subprocess.run(["git", "restore", "file.txt", "other.txt"], check=True, capture_output=True)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    command_apply_from_batch("cleanup")

    assert "line 2 changed" in (paged_file_repo / "file.txt").read_text()
    assert (paged_file_repo / "other.txt").read_text() == "other 1\nother 2\n"


def test_bare_reset_from_batch_after_full_single_file_review_uses_reviewed_file(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()

    command_reset_from_batch("cleanup")

    metadata = read_batch_metadata("cleanup")
    assert set(metadata.get("files", {})) == {"other.txt"}


def test_pathless_include_from_batch_refuses_when_rerendered_batch_diff_changes(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    original_render = hunk_tracking_module.render_batch_file_display

    def changed_render(batch_name, file_path, metadata=None):
        rendered = original_render(batch_name, file_path, metadata=metadata)
        assert rendered is not None
        changed_lines = [
            replace(
                line,
                text_bytes=b"changed after review" if line.id is not None else line.text_bytes,
            )
            for line in rendered.line_changes.lines
        ]
        return RenderedBatchDisplay(
            line_changes=LineLevelChange(
                path=rendered.line_changes.path,
                header=rendered.line_changes.header,
                lines=changed_lines,
            ),
            gutter_to_selection_id=rendered.gutter_to_selection_id,
            selection_id_to_gutter=rendered.selection_id_to_gutter,
        )

    monkeypatch.setattr(hunk_tracking_module, "render_batch_file_display", changed_render)

    with pytest.raises(CommandError, match="no longer matches"):
        command_include_from_batch("cleanup", line_ids=line_spec)


def test_pathless_discard_from_batch_accepts_same_batch_review_selection(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_discard_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert "Discarded selected lines from batch 'cleanup'" in captured.err
    assert read_last_file_review_state() is not None


def test_pathless_discard_from_batch_uses_reviewed_file_in_multi_file_batch(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_discard_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert "Discarded selected lines from batch 'cleanup'" in captured.err
    assert read_last_file_review_state() is not None


def test_pathless_apply_from_batch_uses_reviewed_file_in_multi_file_batch(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    subprocess.run(["git", "restore", "file.txt", "other.txt"], check=True, capture_output=True)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_apply_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert "Applied selected lines from batch 'cleanup'" in captured.err
    assert "line 2 changed" in (paged_file_repo / "file.txt").read_text()
    assert (paged_file_repo / "other.txt").read_text() == "other 1\nother 2\n"


def test_pathless_reset_from_batch_uses_reviewed_file_in_multi_file_batch(
    paged_file_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    _create_multi_file_batch(paged_file_repo)
    capsys.readouterr()
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    command_reset_from_batch("cleanup", line_ids=line_spec)

    captured = capsys.readouterr()
    assert f"Reset line(s) {line_spec} from batch 'cleanup'" in captured.err
    metadata = read_batch_metadata("cleanup")
    assert set(metadata.get("files", {})) == {"file.txt", "other.txt"}


def test_explicit_reset_from_batch_rejects_unshown_review_selection(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert len(state.selections) > 1
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    with pytest.raises(CommandError, match="not valid from the current file review"):
        command_reset_from_batch("cleanup", line_ids=unshown_spec, file="file.txt")

    metadata = read_batch_metadata("cleanup")
    assert "file.txt" in metadata.get("files", {})


@pytest.mark.parametrize(
    "batch_command",
    [
        command_include_from_batch,
        command_apply_from_batch,
        command_discard_from_batch,
    ],
)
def test_explicit_batch_file_line_actions_reject_unshown_review_selection(
    paged_batch_repo,
    monkeypatch,
    capsys,
    batch_command,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert len(state.selections) > 1
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    with pytest.raises(CommandError, match="not valid from the current file review"):
        batch_command("cleanup", line_ids=unshown_spec, file="file.txt")


def test_explicit_include_from_batch_file_line_as_rejects_unshown_review_selection(
    paged_batch_repo,
    monkeypatch,
    capsys,
):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert len(state.selections) > 1
    unshown_spec = format_line_ids(list(state.selections[1].display_ids))

    with pytest.raises(CommandError, match="not valid from the current file review"):
        command_include_from_batch(
            "cleanup",
            line_ids=unshown_spec,
            file="file.txt",
            replacement_text="replacement\n",
        )


def test_pathless_include_from_other_batch_after_batch_review_refuses(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    with pytest.raises(CommandError, match="no longer matches"):
        command_include_from_batch("other-batch", line_ids=line_spec)


def test_pathless_include_from_other_batch_after_full_batch_review_refuses(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_show_from_batch("cleanup", file="file.txt", page="all")
    capsys.readouterr()
    state = read_last_file_review_state()
    assert state is not None
    assert state.entire_file_shown is True
    line_spec = format_line_ids(list(state.selections[0].display_ids))

    with pytest.raises(CommandError, match="no longer matches"):
        command_include_from_batch("other-batch", line_ids=line_spec)


def test_show_from_batch_file_defaults_to_page_review_and_persists_state(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)

    command_show_from_batch("cleanup", file="file.txt")

    captured = capsys.readouterr()
    assert "file.txt  ·  cleanup  ·  page 1/3  ·  change 1/3" in captured.out
    state = read_last_file_review_state()
    assert state is not None
    assert state.source == "batch"
    assert state.shown_pages == (1,)


def test_plain_show_ignores_corrupt_previous_line_state(paged_file_repo, capsys):
    command_start()
    capsys.readouterr()
    get_line_changes_json_file_path().write_text("{ not json")

    command_show()

    captured = capsys.readouterr()
    assert "file.txt" in captured.out


def test_start_clears_batch_review_state_left_outside_session(paged_batch_repo, monkeypatch, capsys):
    _force_one_change_per_page(monkeypatch)
    command_stop()
    capsys.readouterr()
    subprocess.run(["git", "restore", "file.txt"], check=True, capture_output=True)

    command_show_from_batch("cleanup", file="file.txt", page="1")
    capsys.readouterr()
    assert read_last_file_review_state() is not None

    live_file = paged_batch_repo / "live.txt"
    live_file.write_text("live 1\nlive 2\n")
    subprocess.run(["git", "add", "live.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add live file"], check=True, capture_output=True)
    live_file.write_text("live changed\nlive 2\n")

    command_start(quiet=True)

    assert read_last_file_review_state() is None
    assert get_selected_change_file_path() == "live.txt"

    command_include_line("1")

    captured = capsys.readouterr()
    assert "Included line(s): 1" in captured.err


def test_batch_review_model_keeps_unselectable_changed_rows(capsys):
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=10,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"unmergeable\n",
                text="unmergeable\n",
            ),
        ],
    )

    model = build_file_review_model(line_changes, gutter_to_selection_id={})

    assert len(model.changes) == 1
    assert model.changes[0].display_ids == ()
    assert model.changes[0].selection_ids == (10,)

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Localized batch label",
        page_spec="all",
        command_source_args=" --from cleanup",
        source=ReviewSource.BATCH,
        batch_name="cleanup",
    )

    captured = capsys.readouterr()
    assert "not currently selectable" in captured.out
    assert "unmergeable" in captured.out


def test_file_review_rows_use_diff_colors(capsys, monkeypatch):
    """Page-aware reviews should color rows like hunk display."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=1,
                kind="-",
                old_line_number=1,
                new_line_number=None,
                text_bytes=b"old",
                text="old",
            ),
            LineEntry(
                id=2,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"new",
                text="new",
            ),
        ],
    )
    model = build_file_review_model(line_changes)
    monkeypatch.setattr(Colors, "enabled", lambda: True)

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Changes: working tree",
        page_spec="all",
        source=ReviewSource.FILE_VS_HEAD,
    )

    captured = capsys.readouterr()
    assert f"{Colors.GRAY}[#1]{Colors.RESET}" in captured.out
    assert f"{Colors.RED} - old{Colors.RESET}" in captured.out
    assert f"{Colors.GREEN} + new{Colors.RESET}" in captured.out
    assert (
        f"{Colors.BOLD}file.txt  ·  working tree  ·  page 1/1  ·  "
        f"change 1/1  ·  lines 1–2{Colors.RESET}"
    ) in captured.out
    assert (
        f"{Colors.CYAN}include{Colors.RESET}  "
        f"git-stage-batch {Colors.BOLD}include{Colors.RESET} "
        f"--line {Colors.BOLD}1-2{Colors.RESET}"
    ) in captured.out


def test_file_review_multiline_note_is_part_of_header(capsys):
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=0, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"new",
                text="new",
            ),
        ],
    )
    model = build_file_review_model(line_changes)

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Changes: batch cleanup",
        page_spec="all",
        command_source_args=" --from cleanup",
        source=ReviewSource.BATCH,
        batch_name="cleanup",
        note="first line\nsecond line",
    )

    captured = capsys.readouterr()
    assert "Note:\n    first line\n    second line\n" in captured.out


def test_oversized_review_change_splits_without_partial_actions(capsys, monkeypatch):
    from git_stage_batch.output import file_review

    lines = [
        LineEntry(
            id=index,
            kind="+",
            old_line_number=None,
            new_line_number=index,
            text_bytes=f"line {index}".encode(),
            text=f"line {index}",
        )
        for index in range(1, 9)
    ]
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=0, new_start=1, new_len=8),
        lines=lines,
    )
    monkeypatch.setattr(file_review, "_body_budget", lambda: 6)

    model = build_file_review_model(line_changes)

    assert len(model.pages) == 2
    assert model.changes[0].first_page == 1
    assert model.changes[0].last_page == 2

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Changes: working tree",
        page_spec="1",
        source=ReviewSource.FILE_VS_HEAD,
    )

    captured = capsys.readouterr()
    assert "file.txt  ·  working tree  ·  page 1/2  ·  change 1/1  ·  lines 1–4" in captured.out
    assert "Change 1/1   lines 1–4   4-line partial group" in captured.out
    assert "No complete change is actionable from this page." in captured.out
    assert "git-stage-batch include --line" not in captured.out


def test_batch_review_model_splits_visible_runs_around_hidden_rows(capsys):
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=0, new_start=1, new_len=3),
        lines=[
            LineEntry(
                id=10,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"first\n",
                text="first\n",
            ),
            LineEntry(
                id=11,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"hidden\n",
                text="hidden\n",
            ),
            LineEntry(
                id=12,
                kind="+",
                old_line_number=None,
                new_line_number=3,
                text_bytes=b"third\n",
                text="third\n",
            ),
        ],
    )

    model = build_file_review_model(line_changes, gutter_to_selection_id={1: 10, 2: 12})

    assert [change.display_ids for change in model.changes] == [(1,), (), (2,)]

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Localized batch label",
        page_spec="all",
        command_source_args=" --from cleanup",
        source=ReviewSource.BATCH,
        batch_name="cleanup",
    )

    captured = capsys.readouterr()
    assert "Change 1/3   lines 1   1-line change" in captured.out
    assert "Change 2/3   not currently selectable" in captured.out
    assert "Change 3/3   lines 2   1-line change" in captured.out
    hidden_rows = [
        line
        for line in captured.out.splitlines()
        if "hidden" in line
    ]
    assert hidden_rows
    assert all("[#" not in line for line in hidden_rows)


def test_batch_footer_commands_do_not_depend_on_source_label(capsys):
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=1, old_len=0, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=10,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"mergeable\n",
                text="mergeable\n",
            ),
        ],
    )
    model = build_file_review_model(line_changes, gutter_to_selection_id={1: 10})

    print_file_review(
        model,
        shown_pages=(1,),
        source_label="Localized batch label",
        page_spec="all",
        command_source_args=" --from cleanup",
        source=ReviewSource.BATCH,
        batch_name="cleanup",
    )

    captured = capsys.readouterr()
    assert "git-stage-batch include --from cleanup --line 1" in captured.out
    assert "git-stage-batch discard --from cleanup --line 1" in captured.out


def test_batch_review_does_not_suggest_partial_non_adjacent_atomic_replacement(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    test_file = tmp_path / "file.txt"
    test_file.write_text("context\nold later\nanchor\n")
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, capture_output=True)

    ensure_state_directory_exists()
    initialize_abort_state()
    create_batch("atomic")

    test_file.write_text("new first\ncontext\nanchor\n")
    add_file_to_batch(
        "atomic",
        "file.txt",
        BatchOwnership.from_presence_lines(
            ["1"],
            [
                DeletionClaim(anchor_line=3, content_lines=[b"old later\n"]),
            ],
            replacement_units=[
                ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
            ],
        ),
        "100644",
    )
    test_file.write_text("context\nold later\nanchor\n")

    command_show_from_batch("atomic", file="file.txt", page="all")

    captured = capsys.readouterr()
    assert "git-stage-batch include --from atomic --file file.txt --line 1\n" not in captured.out
    assert "git-stage-batch include --from atomic --file file.txt --line 2\n" not in captured.out

    state = read_last_file_review_state()
    assert state is not None
    assert all(set(selection.display_ids) != {1} for selection in state.selections)
    assert all(set(selection.display_ids) != {2} for selection in state.selections)

    with pytest.raises(CommandError, match="only partly selects|not valid"):
        command_include_from_batch("atomic", line_ids="1")


def test_batch_review_preserves_mergeable_change_next_to_reset_only_change():
    """Mixed action neighbors should not collapse into one reset-only change."""
    line_changes = LineLevelChange(
        path="file.txt",
        header=HunkHeader(old_start=0, old_len=0, new_start=1, new_len=2),
        lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"mergeable",
                text="mergeable",
                source_line=1,
            ),
            LineEntry(
                id=2,
                kind="+",
                old_line_number=None,
                new_line_number=2,
                text_bytes=b"reset only",
                text="reset only",
                source_line=2,
            ),
        ],
    )
    review_action_groups = (
        ReviewActionGroup(
            display_ids=(1,),
            selection_ids=(1,),
            actions=(
                FileReviewAction.INCLUDE_FROM_BATCH.value,
                FileReviewAction.DISCARD_FROM_BATCH.value,
                FileReviewAction.APPLY_FROM_BATCH.value,
                FileReviewAction.RESET_FROM_BATCH.value,
            ),
        ),
        ReviewActionGroup(
            display_ids=(2,),
            selection_ids=(2,),
            actions=(FileReviewAction.RESET_FROM_BATCH.value,),
        ),
    )
    model = build_file_review_model(
        line_changes,
        gutter_to_selection_id={1: 1, 2: 2},
        review_action_groups=review_action_groups,
    )
    review_state = make_file_review_state(
        model,
        source=ReviewSource.BATCH,
        batch_name="cleanup",
        shown_pages=(1,),
        selected_change_kind=SelectedChangeKind.BATCH_FILE,
        gutter_to_selection_id={1: 1, 2: 2},
        review_action_groups=review_action_groups,
    )

    assert shown_complete_review_selection_groups(
        review_state,
        FileReviewAction.INCLUDE_FROM_BATCH,
    ) == [{1}]
    assert shown_complete_review_selection_groups(
        review_state,
        FileReviewAction.RESET_FROM_BATCH,
    ) == [{1}, {2}]


def test_show_from_batch_line_after_review_uses_review_id_space(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Filtered show should accept IDs from the last batch review."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    ensure_state_directory_exists()

    test_file = tmp_path / "file.txt"
    test_file.write_text("one\ntwo\n")
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
    initialize_abort_state()

    test_file.write_text("one changed\ntwo changed\n")
    create_batch("manual")
    command_start()
    add_file_to_batch(
        "manual",
        "file.txt",
        BatchOwnership.from_presence_lines(["1", "2"], []),
        "100644",
    )

    original_render = show_from_module.render_batch_file_display

    def render_with_review_only_first_line(batch_name, file_path, metadata=None):
        rendered = original_render(batch_name, file_path, metadata=metadata)
        assert rendered is not None
        return RenderedBatchDisplay(
            line_changes=rendered.line_changes,
            gutter_to_selection_id={1: 2},
            selection_id_to_gutter={2: 1},
            actionable_selection_groups=rendered.actionable_selection_groups,
            review_gutter_to_selection_id={1: 1, 2: 2},
            review_selection_id_to_gutter={1: 1, 2: 2},
            review_action_groups=(
                ReviewActionGroup(
                    display_ids=(1,),
                    selection_ids=(1,),
                    actions=(FileReviewAction.RESET_FROM_BATCH.value,),
                ),
                ReviewActionGroup(
                    display_ids=(2,),
                    selection_ids=(2,),
                    actions=(
                        FileReviewAction.INCLUDE_FROM_BATCH.value,
                        FileReviewAction.DISCARD_FROM_BATCH.value,
                        FileReviewAction.APPLY_FROM_BATCH.value,
                        FileReviewAction.RESET_FROM_BATCH.value,
                    ),
                ),
            ),
        )

    monkeypatch.setattr(show_from_module, "render_batch_file_display", render_with_review_only_first_line)
    monkeypatch.setattr(hunk_tracking_module, "render_batch_file_display", render_with_review_only_first_line)

    command_show_from_batch("manual", file="file.txt", page="all")
    capsys.readouterr()

    command_show_from_batch("manual", file="file.txt", line_ids="2")

    captured = capsys.readouterr()
    assert "[#2]" in captured.out
    assert "two" in captured.out
    assert "one" not in captured.out


def test_show_from_batch_line_without_review_uses_printed_review_id_space(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Filtered batch show should persist the same IDs it prints."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    ensure_state_directory_exists()

    test_file = tmp_path / "file.txt"
    test_file.write_text("one\ntwo\n")
    subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add file"], check=True, capture_output=True)
    initialize_abort_state()

    test_file.write_text("one changed\ntwo changed\n")
    create_batch("manual")
    command_start()
    add_file_to_batch(
        "manual",
        "file.txt",
        BatchOwnership.from_presence_lines(["1", "2"], []),
        "100644",
    )
    test_file.write_text("one\ntwo\n")
    capsys.readouterr()

    def render_with_review_only_second_line(batch_name, file_path, metadata=None):
        line_changes = LineLevelChange(
            path=file_path,
            header=HunkHeader(old_start=0, old_len=0, new_start=1, new_len=2),
            lines=(
                LineEntry(
                    id=1,
                    kind="+",
                    old_line_number=None,
                    new_line_number=1,
                    text_bytes=b"one changed",
                    text="one changed",
                ),
                LineEntry(
                    id=2,
                    kind="+",
                    old_line_number=None,
                    new_line_number=2,
                    text_bytes=b"two changed",
                    text="two changed",
                ),
            ),
        )
        return RenderedBatchDisplay(
            line_changes=line_changes,
            gutter_to_selection_id={1: 2},
            selection_id_to_gutter={2: 1},
            actionable_selection_groups=((2,),),
            review_gutter_to_selection_id={1: 1, 2: 2},
            review_selection_id_to_gutter={1: 1, 2: 2},
            review_action_groups=(
                ReviewActionGroup(
                    display_ids=(1,),
                    selection_ids=(1,),
                    actions=(FileReviewAction.RESET_FROM_BATCH.value,),
                ),
                ReviewActionGroup(
                    display_ids=(2,),
                    selection_ids=(2,),
                    actions=(
                        FileReviewAction.INCLUDE_FROM_BATCH.value,
                        FileReviewAction.DISCARD_FROM_BATCH.value,
                        FileReviewAction.APPLY_FROM_BATCH.value,
                        FileReviewAction.RESET_FROM_BATCH.value,
                    ),
                ),
            ),
        )

    monkeypatch.setattr(show_from_module, "render_batch_file_display", render_with_review_only_second_line)
    monkeypatch.setattr(hunk_tracking_module, "render_batch_file_display", render_with_review_only_second_line)

    command_show_from_batch("manual", file="file.txt", line_ids="2")

    captured = capsys.readouterr()
    assert "[#2]" in captured.out
    assert "two" in captured.out
    assert "one" not in captured.out

    state = read_last_file_review_state()
    assert state is not None
    assert any(selection.display_ids == (2,) for selection in state.selections)

    command_include_from_batch("manual", line_ids="2")

    captured = capsys.readouterr()
    assert "Staged selected lines from batch 'manual'" in captured.err
