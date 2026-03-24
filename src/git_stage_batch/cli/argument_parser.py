"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import subprocess
import sys

from .. import __version__
from .. import commands
from ..i18n import _


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
        'if': ['include', '--file'],
        'il': ['include', '--line'],
        'sf': ['skip', '--file'],
        'sl': ['skip', '--line'],
        'df': ['discard', '--file'],
    }

    # Expand quick actions
    expanded = []
    for arg in args:
        if arg in quick_actions:
            expanded.extend(quick_actions[arg])
        else:
            expanded.append(arg)

    # Create parser
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
        exit_on_error=False,
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
        "-U",
        "--unified",
        dest="context_lines",
        type=int,
        metavar="N",
        help=_("Number of context lines in diff output (default: 3)"),
    )
    parser_start.set_defaults(func=lambda args: commands.command_start(context_lines=args.context_lines))

    # stop - Stop the selected session and clear state
    parser_stop = subparsers.add_parser(
        "stop",
        help=_("Stop the selected session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: commands.command_stop())

    # again - Clear state and start a fresh pass
    parser_again = subparsers.add_parser(
        "again",
        aliases=["a"],
        help=_("Clear state and start a fresh pass"),
    )
    parser_again.set_defaults(func=lambda _: commands.command_again())

    # show - Show the selected hunk
    parser_show = subparsers.add_parser(
        "show",
        help=_("Show the selected hunk"),
    )
    parser_show.set_defaults(func=lambda _: commands.command_show())

    # status - Show selected session status
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help=_("Show selected session status"),
    )
    parser_status.set_defaults(func=lambda _: commands.command_status())

    # include - Stage the selected hunk
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i"],
        help=_("Stage the selected hunk"),
    )
    parser_include.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Stage only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_include.add_argument(
        "--file",
        action="store_true",
        help=_("Stage the entire file containing the selected hunk"),
    )
    parser_include.set_defaults(func=lambda args: (
        commands.command_include_line(args.line_ids) if args.line_ids
        else commands.command_include_file() if args.file
        else commands.command_include()
    ))

    # skip - Skip the selected hunk without staging
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s"],
        help=_("Skip the selected hunk without staging"),
    )
    parser_skip.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Skip only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_skip.add_argument(
        "--file",
        action="store_true",
        help=_("Skip all hunks from the selected file"),
    )
    parser_skip.set_defaults(func=lambda args: (
        commands.command_skip_line(args.line_ids) if args.line_ids
        else commands.command_skip_file() if args.file
        else commands.command_skip()
    ))

    # discard - Remove the selected hunk from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d"],
        help=_("Remove the selected hunk from working tree"),
    )
    parser_discard.add_argument(
        "--file",
        action="store_true",
        help=_("Discard the entire file containing the selected hunk"),
    )
    parser_discard.set_defaults(func=lambda args: (
        commands.command_discard_file() if args.file
        else commands.command_discard()
    ))

    # abort - Restore repository to pre-session state
    parser_abort = subparsers.add_parser(
        "abort",
        help=_("Restore repository to pre-session state"),
    )
    parser_abort.set_defaults(func=lambda _: commands.command_abort())

    # block-file - Permanently exclude a file
    parser_block_file = subparsers.add_parser(
        "block-file",
        aliases=["bf"],
        help=_("Permanently exclude a file (adds to .gitignore)"),
    )
    parser_block_file.add_argument(
        "file_path",
        help=_("Path to the file to block"),
    )
    parser_block_file.set_defaults(func=lambda args: commands.command_block_file(args.file_path))

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
