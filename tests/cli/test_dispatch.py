"""Tests for CLI command dispatch."""

import argparse

import pytest

import git_stage_batch.cli.dispatch as dispatch
from git_stage_batch.exceptions import CommandError


def test_dispatch_args_no_command(monkeypatch):
    """Test dispatch with no command raises CommandError."""
    args = argparse.Namespace(command=None)

    class _FakePath:
        def exists(self) -> bool:
            return False

    monkeypatch.setattr(dispatch, "get_abort_head_file_path", lambda: _FakePath())

    with pytest.raises(CommandError) as exc_info:
        dispatch.dispatch_args(args)
    assert "No batch staging session in progress" in exc_info.value.message


def test_dispatch_args_callable():
    """Test that dispatch_args is callable."""
    assert dispatch.dispatch_args is not None
    assert callable(dispatch.dispatch_args)


def test_dispatch_args_with_command():
    """Test dispatch executes command function."""
    executed = []

    def mock_command(args):
        executed.append(True)

    args = argparse.Namespace(command="test", func=mock_command)
    dispatch.dispatch_args(args)
    assert executed == [True]


def test_dispatch_args_no_command_shows_selected_hunk_when_session_active(monkeypatch):
    """Test dispatch with no command falls back to show during an active session."""
    args = argparse.Namespace(command=None)
    called = []

    monkeypatch.setattr(dispatch, "command_show", lambda: called.append(True))

    class _FakePath:
        def exists(self) -> bool:
            return True

    monkeypatch.setattr(dispatch, "get_abort_head_file_path", lambda: _FakePath())

    dispatch.dispatch_args(args)
    assert called == [True]
