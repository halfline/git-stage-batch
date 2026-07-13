"""CLI command escape for interactive mode."""

from __future__ import annotations

from collections.abc import Callable
import os
import shlex

from ..cli.argument_parser import parse_command_line
from ..cli.execution import execute_non_interactive_args
from ..exceptions import BypassRefresh
from ..i18n import _
from ..data.session_ownership import require_no_foreign_session_owner
from ..utils.session_lock import acquire_session_lock


_READ_ONLY_COMMANDS = frozenset(
    {
        "check-unstaged",
        "journal",
        "list",
        "show",
        "status",
        "validate",
    }
)


def _execute_embedded_args(args) -> None:
    """Apply normal cwd and target-repository locking for one TUI command."""
    original_cwd = os.getcwd()
    try:
        working_directory = getattr(args, "working_directory", None)
        if working_directory is not None:
            os.chdir(working_directory)
        skip_lock = getattr(args, "prompt_format", None) is not None
        if skip_lock:
            execute_non_interactive_args(args)
            return
        with acquire_session_lock():
            if getattr(args, "command", None) not in _READ_ONLY_COMMANDS:
                require_no_foreign_session_owner()
            execute_non_interactive_args(args)
    finally:
        os.chdir(original_cwd)


def handle_cli_escape(action: str, *, print_help: Callable[[], None]) -> None:
    """Handle arbitrary CLI command as an interactive escape hatch."""
    try:
        args_list = shlex.split(action)
        try:
            args = parse_command_line(args_list, quiet=False)
        except SystemExit as error:
            # argparse has already rendered help, version, or diagnostics.
            if error.code not in (0, 2):
                print(
                    _("\nCommand exited with status {status}").format(status=error.code)
                )
            raise BypassRefresh() from None

        if args is not None:
            if getattr(args, "interactive_flag", False) or getattr(
                args, "interactive_command", False
            ):
                print(_("\nAlready in interactive mode."))
            else:
                _execute_embedded_args(args)
        else:
            print(_("\nUnknown command: '{cmd}'").format(cmd=action))
            print_help()
    except Exception as error:
        print(_("\nError executing command: {error}").format(error=error))
    raise BypassRefresh()
