"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from . import commands
from .i18n import _
from .state import get_state_directory_path, require_git_repository


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
    parser_start.add_argument(
        "-U", "--unified",
        type=int,
        default=3,
        metavar="N",
        help=_("Number of context lines in diff output (default: 3)"),
    )
    parser_start.set_defaults(func=lambda args: commands.command_start(unified=args.unified))

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
        help=_("Clear state and start a fresh pass"),
    )
    parser_again.set_defaults(func=lambda _: commands.command_again())

    # show - Show the current hunk
    parser_show = subparsers.add_parser(
        "show",
        help=_("Show the current hunk"),
    )
    parser_show.set_defaults(func=lambda _: commands.command_show())

    # include - Include (stage) the current hunk
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i"],
        help=_("Include (stage) the current hunk"),
    )
    parser_include.set_defaults(func=lambda _: commands.command_include())

    # skip - Skip the current hunk without staging
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s"],
        help=_("Skip the current hunk without staging"),
    )
    parser_skip.set_defaults(func=lambda _: commands.command_skip())

    # discard - Discard the current hunk from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d"],
        help=_("Discard the current hunk from working tree"),
    )
    parser_discard.set_defaults(func=lambda _: commands.command_discard())

    # status - Show current session status
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help=_("Show current session status"),
    )
    parser_status.set_defaults(func=lambda _: commands.command_status())

    # abort - Abort session and restore repository state
    parser_abort = subparsers.add_parser(
        "abort",
        help=_("Abort session and undo all changes"),
    )
    parser_abort.set_defaults(func=lambda _: commands.command_abort())

    args = parser.parse_args()

    if args.command is None:
        # No command provided - check if session is active
        require_git_repository()  # This will print error and exit if not in a git repo

        if get_state_directory_path().exists():
            # Default to include when session is active
            commands.command_include()
        else:
            # No session - show helpful message
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
            sys.exit(1)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
