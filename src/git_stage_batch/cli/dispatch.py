"""Command dispatch logic."""

from __future__ import annotations

import argparse

from ..exceptions import exit_with_error
from ..i18n import _


def dispatch_args(args: argparse.Namespace) -> None:
    """Execute the command based on parsed arguments.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    # Check for -i flag first
    if hasattr(args, 'interactive_flag') and args.interactive_flag:
        from ..commands import command_interactive
        command_interactive()
    elif args.command is None:
        # No command provided - show helpful message
        exit_with_error(
            _("No batch staging session in progress.") + "\n" +
            _("Run 'git-stage-batch start' to begin.")
        )
    else:
        args.func(args)
