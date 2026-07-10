"""Tests for parsed command execution."""

from __future__ import annotations

import argparse

import pytest

import git_stage_batch.cli.execution as execution
from git_stage_batch.exceptions import CommandError


def test_execute_noninteractive_args_no_command(monkeypatch):
    """No command should raise unless an active selected hunk exists."""
    args = argparse.Namespace(command=None)

    class _FakePath:
        def exists(self) -> bool:
            return False

    monkeypatch.setattr(execution, "get_abort_head_file_path", lambda: _FakePath())

    with pytest.raises(CommandError) as exc_info:
        execution.execute_non_interactive_args(args)

    assert "No batch staging session in progress" in exc_info.value.message


def test_execute_noninteractive_args_with_command():
    """Command args should execute their parsed function."""
    executed = []

    def mock_command(args):
        executed.append(args)

    args = argparse.Namespace(command="test", func=mock_command)
    execution.execute_non_interactive_args(args)

    assert executed == [args]


def test_execute_noninteractive_args_no_command_shows_selected_hunk(monkeypatch):
    """No command should fall back to show during an active session."""
    args = argparse.Namespace(command=None)
    called = []

    monkeypatch.setattr(execution, "command_show", lambda: called.append(True))

    class _FakePath:
        def exists(self) -> bool:
            return True

    monkeypatch.setattr(execution, "get_abort_head_file_path", lambda: _FakePath())

    execution.execute_non_interactive_args(args)

    assert called == [True]
