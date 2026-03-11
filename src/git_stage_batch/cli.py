"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from . import commands
from .i18n import _


class GitHelpArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that tries to use git help for --help."""

    def print_help(self, file=None):
        """Try to use git help, fall back to argparse help."""
        try:
            result = subprocess.run(
                ["git", "help", "stage-batch"],
                check=False,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return
        except (FileNotFoundError, OSError):
            pass

        # Fall back to standard argparse help
        super().print_help(file)


def main() -> None:
    """Main entry point for git-stage-batch."""
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help=_("Available commands"),
    )

    # start - Start a new batch staging session
    parser_start = subparsers.add_parser(
        "start",
        help=_("Start a new batch staging session"),
    )
    parser_start.set_defaults(func=lambda _: commands.command_start())

    # stop - Stop the current session and clear state
    parser_stop = subparsers.add_parser(
        "stop",
        help=_("Stop the current session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: commands.command_stop())

    # again - Clear state and start a fresh pass
    parser_again = subparsers.add_parser(
        "again",
        aliases=["a"],
        help="Clear state and start a fresh pass",
    )
    parser_again.set_defaults(func=lambda _: commands.command_again())

    args = parser.parse_args()

    if args.command is None:
        # No command provided - show helpful message
        print(_("No batch staging session in progress."), file=sys.stderr)
        print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        sys.exit(1)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
