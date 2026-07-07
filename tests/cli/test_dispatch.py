"""Tests for CLI command dispatch."""

import argparse

import git_stage_batch.cli.dispatch as dispatch


def test_dispatch_args_no_command(monkeypatch):
    """Noninteractive args should be delegated to the execution layer."""
    args = argparse.Namespace(command=None)
    called = []

    monkeypatch.setattr(
        dispatch,
        "execute_non_interactive_args",
        lambda delegated_args: called.append(delegated_args),
    )

    dispatch.dispatch_args(args)

    assert called == [args]


def test_dispatch_args_callable():
    """Test that dispatch_args is callable."""
    assert dispatch.dispatch_args is not None
    assert callable(dispatch.dispatch_args)


def test_dispatch_args_with_command(monkeypatch):
    """Commands should be delegated to the execution layer."""
    executed = []

    def fake_execute(args):
        executed.append(args)

    args = argparse.Namespace(command="test")
    monkeypatch.setattr(dispatch, "execute_non_interactive_args", fake_execute)
    dispatch.dispatch_args(args)
    assert executed == [args]


def test_dispatch_args_interactive_flag_uses_interactive_runner(monkeypatch):
    """The top-level interactive flag should launch through dispatch."""
    called = []
    args = argparse.Namespace(command=None, interactive_flag=True)

    monkeypatch.setattr(
        dispatch,
        "_run_interactive_command",
        lambda: called.append(True),
    )

    dispatch.dispatch_args(args)

    assert called == [True]


def test_dispatch_args_interactive_command_uses_interactive_runner(monkeypatch):
    """The interactive subcommand should launch through dispatch."""
    called = []
    args = argparse.Namespace(
        command="interactive",
        interactive_command=True,
        interactive_flag=False,
    )

    monkeypatch.setattr(
        dispatch,
        "_run_interactive_command",
        lambda: called.append(True),
    )

    dispatch.dispatch_args(args)

    assert called == [True]


def test_run_interactive_command_launches_tui(monkeypatch):
    """The dispatch runner should call the TUI entry point directly."""
    called = []

    monkeypatch.setattr(
        "git_stage_batch.tui.interactive.start_interactive_mode",
        lambda: called.append(True),
    )

    dispatch._run_interactive_command()

    assert called == [True]
