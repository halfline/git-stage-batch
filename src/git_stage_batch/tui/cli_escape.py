"""CLI command escape for interactive mode."""

from __future__ import annotations

from collections.abc import Callable
import shlex

from ..cli.argument_parser import parse_command_line
from ..cli.execution import execute_non_interactive_args
from ..exceptions import BypassRefresh
from ..i18n import _


def handle_cli_escape(action: str, *, print_help: Callable[[], None]) -> None:
    """Handle arbitrary CLI command as an interactive escape hatch."""
    try:
        args_list = shlex.split(action)
        args = parse_command_line(args_list, quiet=False)

        if args is not None:
            if (
                getattr(args, "interactive_flag", False)
                or getattr(args, "interactive_command", False)
            ):
                print(_("\nAlready in interactive mode."))
            else:
                execute_non_interactive_args(args)
        else:
            print(_("\nUnknown command: '{cmd}'").format(cmd=action))
            print_help()
    except Exception as error:
        print(_("\nError executing command: {error}").format(error=error))
    raise BypassRefresh()
