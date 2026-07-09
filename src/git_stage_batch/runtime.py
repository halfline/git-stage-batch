"""Runtime mode dispatch between CLI parsing and interactive TUI launch."""

from __future__ import annotations

import argparse

from .cli.execution import execute_non_interactive_args


def _run_interactive_command() -> None:
    from .tui.interactive import start_interactive_mode

    start_interactive_mode()


def dispatch_cli_mode(args: argparse.Namespace) -> None:
    """Route parsed arguments to interactive or noninteractive execution.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    # Check interactive launch paths first.
    if (
        getattr(args, "interactive_flag", False)
        or getattr(args, "interactive_command", False)
    ):
        _run_interactive_command()
    else:
        execute_non_interactive_args(args)
