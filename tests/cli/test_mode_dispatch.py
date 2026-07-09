"""Tests for CLI mode dispatch."""

import argparse

import git_stage_batch.cli.mode_dispatch as mode_dispatch


def test_dispatch_cli_mode_no_command(monkeypatch):
    """Noninteractive args should be delegated to the execution layer."""
    args = argparse.Namespace(command=None)
    called = []

    monkeypatch.setattr(
        mode_dispatch,
        "execute_non_interactive_args",
        lambda delegated_args: called.append(delegated_args),
    )

    mode_dispatch.dispatch_cli_mode(args)

    assert called == [args]


def test_dispatch_cli_mode_callable():
    """Test that dispatch_cli_mode is callable."""
    assert mode_dispatch.dispatch_cli_mode is not None
    assert callable(mode_dispatch.dispatch_cli_mode)


def test_dispatch_cli_mode_with_command(monkeypatch):
    """Commands should be delegated to the execution layer."""
    executed = []

    def fake_execute(args):
        executed.append(args)

    args = argparse.Namespace(command="test")
    monkeypatch.setattr(mode_dispatch, "execute_non_interactive_args", fake_execute)
    mode_dispatch.dispatch_cli_mode(args)
    assert executed == [args]


def test_dispatch_cli_mode_interactive_flag_uses_interactive_runner(monkeypatch):
    """The top-level interactive flag should launch through dispatch."""
    called = []
    args = argparse.Namespace(command=None, interactive_flag=True)

    monkeypatch.setattr(
        mode_dispatch,
        "_run_interactive_command",
        lambda: called.append(True),
    )

    mode_dispatch.dispatch_cli_mode(args)

    assert called == [True]


def test_dispatch_cli_mode_interactive_command_uses_interactive_runner(monkeypatch):
    """The interactive subcommand should launch through dispatch."""
    called = []
    args = argparse.Namespace(
        command="interactive",
        interactive_command=True,
        interactive_flag=False,
    )

    monkeypatch.setattr(
        mode_dispatch,
        "_run_interactive_command",
        lambda: called.append(True),
    )

    mode_dispatch.dispatch_cli_mode(args)

    assert called == [True]


def test_run_interactive_command_launches_tui(monkeypatch):
    """The dispatch runner should call the TUI entry point directly."""
    called = []

    monkeypatch.setattr(
        "git_stage_batch.tui.interactive.start_interactive_mode",
        lambda: called.append(True),
    )

    mode_dispatch._run_interactive_command()

    assert called == [True]
