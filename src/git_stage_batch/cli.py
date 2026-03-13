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
    parser_show.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Exit with status code only (0=hunk exists, 1=no hunk)"),
    )
    parser_show.set_defaults(func=lambda args: commands.command_show(porcelain=args.porcelain))

    # include - Include (stage) the current hunk, specific lines, or entire file
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i"],
        help=_("Include (stage) the current hunk"),
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
        help=_("Stage the entire file containing the current hunk"),
    )
    parser_include.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'il IDS' alias support
    )
    parser_include.set_defaults(func=lambda args: (
        commands.command_include_file() if args.file
        else commands.command_include_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else commands.command_include()
    ))

    # include-line - Include (stage) specific lines (hidden, exists only for 'il' alias)
    parser_include_line = subparsers.add_parser(
        "include-line",
        aliases=["il"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'il' alias
    )
    parser_include_line.add_argument(
        "line_ids",
        help=_("Line IDs to include (e.g., '1,3,5-7')"),
    )
    parser_include_line.set_defaults(func=lambda args: commands.command_include_line(args.line_ids))

    # include-file - Include (stage) all hunks from current file
    parser_include_file = subparsers.add_parser(
        "include-file",
        aliases=["if"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'if' alias
    )
    parser_include_file.set_defaults(func=lambda _: commands.command_include_file())

    # skip - Skip the current hunk, specific lines, or entire file
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s"],
        help=_("Skip the current hunk without staging"),
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
        help=_("Skip the entire file containing the current hunk"),
    )
    parser_skip.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'sl IDS' alias support
    )
    parser_skip.set_defaults(func=lambda args: (
        commands.command_skip_file() if args.file
        else commands.command_skip_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else commands.command_skip()
    ))

    # skip-line - Skip specific lines (hidden, exists only for 'sl' alias)
    parser_skip_line = subparsers.add_parser(
        "skip-line",
        aliases=["sl"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'sl' alias
    )
    parser_skip_line.add_argument(
        "line_ids",
        help=_("Line IDs to skip (e.g., '1,3,5-7')"),
    )
    parser_skip_line.set_defaults(func=lambda args: commands.command_skip_line(args.line_ids))

    # skip-file - Skip all hunks from current file
    parser_skip_file = subparsers.add_parser(
        "skip-file",
        aliases=["sf"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'sf' alias
    )
    parser_skip_file.set_defaults(func=lambda _: commands.command_skip_file())

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

    # unblock-file - Remove a file from .gitignore and blocked list
    parser_unblock_file = subparsers.add_parser(
        "unblock-file",
        aliases=["ub"],
        help=_("Remove a file from .gitignore and blocked list"),
    )
    parser_unblock_file.add_argument(
        "file_path",
        help=_("Path to the file to unblock"),
    )
    parser_unblock_file.set_defaults(func=lambda args: commands.command_unblock_file(args.file_path))

    # discard - Discard the current hunk, specific lines, or entire file from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d"],
        help=_("Discard the current hunk from working tree"),
    )
    parser_discard.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Discard only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_discard.add_argument(
        "--file",
        action="store_true",
        help=_("Discard the entire file containing the current hunk"),
    )
    parser_discard.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'dl IDS' alias support
    )
    parser_discard.set_defaults(func=lambda args: (
        commands.command_discard_file() if args.file
        else commands.command_discard_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else commands.command_discard()
    ))

    # discard-line - Discard specific lines (hidden, exists only for 'dl' alias)
    parser_discard_line = subparsers.add_parser(
        "discard-line",
        aliases=["dl"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'dl' alias
    )
    parser_discard_line.add_argument(
        "line_ids",
        help=_("Line IDs to discard (e.g., '1,3,5-7')"),
    )
    parser_discard_line.set_defaults(func=lambda args: commands.command_discard_line(args.line_ids))

    # discard-file - Discard all hunks from current file
    parser_discard_file = subparsers.add_parser(
        "discard-file",
        aliases=["df"],
        help=argparse.SUPPRESS,  # Hidden, exists only for 'df' alias
    )
    parser_discard_file.set_defaults(func=lambda _: commands.command_discard_file())

    # status - Show current session status
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help=_("Show current session status"),
    )
    parser_status.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output in machine-readable JSON format"),
    )
    parser_status.set_defaults(func=lambda args: commands.command_status(porcelain=args.porcelain))

    # suggest-fixup - Suggest commits to fixup based on current hunk or specific lines
    parser_suggest_fixup = subparsers.add_parser(
        "suggest-fixup",
        aliases=["x"],
        help=_("Suggest which commit the current hunk should be fixed up to"),
    )
    parser_suggest_fixup.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Analyze only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_suggest_fixup.add_argument(
        "--reset",
        action="store_true",
        help=_("Reset state and start search over from most recent"),
    )
    parser_suggest_fixup.add_argument(
        "--abort",
        action="store_true",
        help=_("Clear state and exit without showing candidates"),
    )
    parser_suggest_fixup.add_argument(
        "--last",
        action="store_true",
        help=_("Re-show the last candidate without advancing"),
    )
    parser_suggest_fixup.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_("Git ref to use as lower bound for commit search (default: @{upstream})"),
    )
    parser_suggest_fixup.set_defaults(func=lambda args: (
        commands.command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        ) if args.line_ids else
        commands.command_suggest_fixup(
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        )
    ))

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
