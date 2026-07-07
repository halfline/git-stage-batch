"""Command dispatch logic."""

from __future__ import annotations

import argparse

from ..commands.show import command_show
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.paths import get_abort_head_file_path


def _run_interactive_command() -> None:
    from ..commands.interactive import command_interactive
    command_interactive()


def dispatch_args(args: argparse.Namespace) -> None:
    """Execute the command based on parsed arguments.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    # Check interactive launch paths first.
    if (
        getattr(args, "interactive_flag", False)
        or getattr(args, "interactive_command", False)
    ):
        _run_interactive_command()
    elif args.command is None:
        if get_abort_head_file_path().exists():
            command_show()
        else:
            # No command provided - show helpful message
            exit_with_error(
                _("No batch staging session in progress.") + "\n" +
                _("Run 'git-stage-batch start' to begin.")
            )
    else:
        args.func(args)
