"""CLI entry point for git-stage-batch."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from .i18n import _
from .commands import (
    command_abort,
    command_again,
    command_block_file,
    command_discard,
    command_discard_line,
    command_include,
    command_include_file,
    command_include_line,
    command_interactive,
    command_show,
    command_skip,
    command_skip_file,
    command_skip_line,
    command_start,
    command_status,
    command_stop,
    command_suggest_fixup,
    command_suggest_fixup_line,
    command_unblock_file,
)
from .state import get_current_hunk_patch_file_path


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
    """Main CLI entry point."""
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description="Non-interactive hunk-by-hunk and line-by-line staging for git",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Enter interactive mode (process hunks one by one)",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=False,
        help="Available commands",
    )

    # start - Find and display the first unprocessed hunk
    parser_start = subparsers.add_parser(
        "start",
        help="Find and display the first unprocessed hunk",
        description="Find and display the first unprocessed hunk; cache as 'current'",
    )
    parser_start.add_argument(
        "-U", "--unified",
        type=int,
        default=3,
        metavar="N",
        help="Number of context lines in diff output (default: 3)",
    )
    parser_start.set_defaults(func=lambda args: command_start(unified=args.unified))

    # show - Reprint the cached current hunk
    parser_show = subparsers.add_parser(
        "show",
        aliases=["sh"],
        help="Reprint the cached current hunk",
        description="Reprint the cached 'current' hunk (annotated with line IDs)",
    )
    parser_show.add_argument(
        "--porcelain",
        action="store_true",
        help="Suppress output, exit 0 if hunk exists, 1 if not",
    )
    parser_show.set_defaults(func=lambda args: command_show(porcelain=args.porcelain))

    # include - Stage the current hunk, specific lines, or entire file
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i", "il", "if"],
        help="Stage the current hunk to the index",
        description="Stage the cached hunk (entire hunk) to the index; advance to next",
    )
    parser_include.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help="Stage only specific line IDs (e.g., '1,3,5-7')",
    )
    parser_include.add_argument(
        "--file",
        action="store_true",
        help="Stage the entire file containing the current hunk",
    )
    parser_include.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'il IDS' alias support
    )
    parser_include.set_defaults(func=lambda args: (
        command_include_file() if args.file
        else command_include_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else command_include()
    ))

    # skip - Skip the current hunk, specific lines, or entire file
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s", "sl", "sf"],
        help="Mark the current hunk as skipped",
        description="Mark the cached hunk as skipped; advance to next",
    )
    parser_skip.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help="Skip only specific line IDs (e.g., '1,3,5-7')",
    )
    parser_skip.add_argument(
        "--file",
        action="store_true",
        help="Skip all hunks in the file containing the current hunk",
    )
    parser_skip.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'sl IDS' alias support
    )
    parser_skip.set_defaults(func=lambda args: (
        command_skip_file() if args.file
        else command_skip_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else command_skip()
    ))

    # discard - Remove the current hunk, specific lines from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d", "dl"],
        help="Remove the current hunk from working tree",
        description="Reverse-apply the cached hunk to the working tree; advance to next",
    )
    parser_discard.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help="Discard only specific line IDs (e.g., '1,3,5-7')",
    )
    parser_discard.add_argument(
        "line_ids_positional",
        nargs="?",
        help=argparse.SUPPRESS,  # Hidden positional for 'dl IDS' alias support
    )
    parser_discard.set_defaults(func=lambda args: (
        command_discard_line(args.line_ids or args.line_ids_positional) if (args.line_ids or args.line_ids_positional)
        else command_discard()
    ))

    # block-file - Permanently exclude a file
    parser_block_file = subparsers.add_parser(
        "block-file",
        aliases=["b"],
        help="Permanently exclude a file via .gitignore",
        description="Add a file to .gitignore and blocked list (defaults to current hunk's file)",
    )
    parser_block_file.add_argument(
        "path",
        nargs="?",
        default="",
        help="File path to block (optional, defaults to current hunk's file)",
    )
    parser_block_file.set_defaults(func=lambda args: command_block_file(args.path))

    # unblock-file - Reverse permanent exclusion
    parser_unblock_file = subparsers.add_parser(
        "unblock-file",
        aliases=["ub"],
        help="Remove a file from permanent exclusion",
        description="Remove a file from .gitignore and blocked list",
    )
    parser_unblock_file.add_argument(
        "path",
        help="File path to unblock",
    )
    parser_unblock_file.set_defaults(func=lambda args: command_unblock_file(args.path))

    # again - Clear state and start fresh
    parser_again = subparsers.add_parser(
        "again",
        aliases=["a"],
        help="Clear state and start a fresh pass",
        description="Clear state and immediately start a fresh pass through all hunks",
    )
    parser_again.set_defaults(func=lambda _: command_again())

    # stop - Clear all state
    parser_stop = subparsers.add_parser(
        "stop",
        help="Clear all state",
        description="Clear all state (blocklist and cached hunk)",
    )
    parser_stop.set_defaults(func=lambda _: command_stop())

    # abort - Undo all changes and clear state
    parser_abort = subparsers.add_parser(
        "abort",
        help="Abort session and undo all changes",
        description="Undo all changes including commits and discards, restore to session start state",
    )
    parser_abort.set_defaults(func=lambda _: command_abort())

    # status - Show current state
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help="Show current state",
        description="Show brief state (current hunk summary, remaining line IDs)",
    )
    parser_status.add_argument(
        "--porcelain",
        action="store_true",
        help="Output in machine-readable format (JSON)",
    )
    parser_status.set_defaults(func=lambda args: command_status(porcelain=args.porcelain))

    # suggest-fixup - Suggest which commit to fixup
    parser_suggest_fixup = subparsers.add_parser(
        "suggest-fixup",
        aliases=["x", "sfl"],
        help="Suggest which commit the current hunk should be fixed up to",
        description="Analyze the current hunk and suggest an appropriate commit for --fixup",
    )
    parser_suggest_fixup.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help="Analyze only specific line IDs (e.g., '1,3,5-7')",
    )
    parser_suggest_fixup.add_argument(
        "boundary_or_line_ids",
        nargs="?",
        default="@{upstream}",
        help="Boundary ref for commit search, or line IDs if using sfl alias (default: @{upstream})",
    )
    parser_suggest_fixup.add_argument(
        "boundary_if_line_ids",
        nargs="?",
        default="@{upstream}",
        help=argparse.SUPPRESS,  # Hidden second positional for sfl IDS BOUNDARY
    )

    def suggest_fixup_dispatcher(args):
        # Determine if we're being called as 'sfl' for backward compat
        # If --line flag is used, use that
        if args.line_ids:
            return command_suggest_fixup_line(args.line_ids, args.boundary_or_line_ids)
        # If boundary_or_line_ids looks like line IDs (contains numbers/commas), treat as line IDs
        elif ',' in args.boundary_or_line_ids or args.boundary_or_line_ids.replace('-', '').isdigit():
            # Likely line IDs (backward compat with sfl)
            boundary = args.boundary_if_line_ids
            return command_suggest_fixup_line(args.boundary_or_line_ids, boundary)
        else:
            # It's a boundary ref
            return command_suggest_fixup(args.boundary_or_line_ids)

    parser_suggest_fixup.set_defaults(func=suggest_fixup_dispatcher)


    args = parser.parse_args()

    # Handle --interactive flag
    if args.interactive:
        command_interactive()
        return

    # Handle no command provided
    if args.command is None:
        # Check if a session is active
        if get_current_hunk_patch_file_path().exists():
            # Default to include when session is active
            command_include()
        else:
            # No session - show helpful message
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
            sys.exit(1)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
