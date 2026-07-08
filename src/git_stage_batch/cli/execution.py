"""Parsed command execution without interactive launch handling."""

from __future__ import annotations

import argparse

from ..commands.show import command_show
from ..data.session import session_is_active
from ..exceptions import exit_with_error
from ..i18n import _


def execute_non_interactive_args(args: argparse.Namespace) -> None:
    """Execute parsed command arguments that are not interactive launch requests."""
    if args.command is None:
        if session_is_active():
            command_show()
            return
        exit_with_error(
            _("No batch staging session in progress.") + "\n" +
            _("Run 'git-stage-batch start' to begin.")
        )
    else:
        args.func(args)
