"""Tests for file-review command text helpers."""

from __future__ import annotations

from git_stage_batch.data.file_review.action_commands import (
    live_to_batch_action_command,
    show_command_for_review_state,
)
from git_stage_batch.data.file_review.records import FileReviewState, ReviewSource
from git_stage_batch.data.selected_change.store import SelectedChangeKind
from git_stage_batch.git_paths import terminal_safe_shell_quote


def test_live_to_batch_action_quotes_shell_significant_batch_name():
    """A valid batch name must remain one token in a displayed command."""
    command = live_to_batch_action_command(
        "include",
        "batch;next",
        file_scope=True,
        line_ids="1,2",
    )

    assert command == "include --to 'batch;next' --file --line 1,2"


def test_show_command_quotes_terminal_control_path():
    """A review recovery command must render a hostile path harmlessly."""
    file_path = "evil\x1b[2Jname\nnext.txt"
    review_state = FileReviewState(
        source=ReviewSource.UNSTAGED,
        batch_name=None,
        file_path=file_path,
        page_spec="1",
        shown_pages=(1,),
        page_count=1,
        entire_file_shown=True,
        selections=(),
        selected_change_kind=SelectedChangeKind.FILE,
        selected_file_fingerprint="selected",
        diff_fingerprint="diff",
    )

    command = show_command_for_review_state(review_state)

    assert file_path not in command
    assert "\x1b" not in command
    assert "\nnext.txt" not in command
    assert terminal_safe_shell_quote(file_path) in command
