"""Tests for file-review command text helpers."""

from __future__ import annotations

from git_stage_batch.data.file_review.action_commands import (
    live_to_batch_action_command,
)


def test_live_to_batch_action_quotes_shell_significant_batch_name():
    """A valid batch name must remain one token in a displayed command."""
    command = live_to_batch_action_command(
        "include",
        "batch;next",
        file_scope=True,
        line_ids="1,2",
    )

    assert command == "include --to 'batch;next' --file --line 1,2"
