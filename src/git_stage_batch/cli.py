"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .i18n import _


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    # Mapping from shortcuts to their expanded forms
    quick_actions = {
        '?': ['--help'],
    }

    # Expand quick actions
    expanded = []
    for arg in args:
        if arg in quick_actions:
            expanded.extend(quick_actions[arg])
        else:
            expanded.append(arg)

    # Create parser
    parser = argparse.ArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None


def dispatch_args(args: argparse.Namespace) -> None:
    """Execute the command based on parsed arguments.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    # Currently no subcommands, just version
    pass


def main() -> None:
    """Main entry point for git-stage-batch."""
    args = parse_command_line(sys.argv[1:], quiet=False)
    if args is not None:
        dispatch_args(args)


if __name__ == "__main__":
    main()
