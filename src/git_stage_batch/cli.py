"""CLI entry point for git-stage-batch."""

from __future__ import annotations

import argparse
import sys

from . import __version__
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
    command_unblock_file,
)
from .state import get_current_hunk_patch_file_path


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="git-stage-batch",
        description="Non-interactive hunk-by-hunk and line-by-line staging for git",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--interactive",
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

    # include - Stage the current hunk
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i"],
        help="Stage the current hunk to the index",
        description="Stage the cached hunk (entire hunk) to the index; advance to next",
    )
    parser_include.set_defaults(func=lambda _: command_include())

    # skip - Skip the current hunk
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s"],
        help="Mark the current hunk as skipped",
        description="Mark the cached hunk as skipped; advance to next",
    )
    parser_skip.set_defaults(func=lambda _: command_skip())

    # discard - Remove the current hunk from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d"],
        help="Remove the current hunk from working tree",
        description="Reverse-apply the cached hunk to the working tree; advance to next",
    )
    parser_discard.set_defaults(func=lambda _: command_discard())

    # include-line - Stage specific lines
    parser_include_line = subparsers.add_parser(
        "include-line",
        aliases=["il"],
        help="Stage specific lines from the current hunk",
        description="Stage ONLY the listed changed line IDs (+/-) to the index",
    )
    parser_include_line.add_argument(
        "line_ids",
        help="Line IDs to include (e.g., '1,3,5-7')",
    )
    parser_include_line.set_defaults(func=lambda args: command_include_line(args.line_ids))

    # skip-line - Skip specific lines
    parser_skip_line = subparsers.add_parser(
        "skip-line",
        aliases=["sl"],
        help="Mark specific lines as skipped",
        description="Mark ONLY the listed changed line IDs as skipped",
    )
    parser_skip_line.add_argument(
        "line_ids",
        help="Line IDs to skip (e.g., '1,3,5-7')",
    )
    parser_skip_line.set_defaults(func=lambda args: command_skip_line(args.line_ids))

    # discard-line - Remove specific lines from working tree
    parser_discard_line = subparsers.add_parser(
        "discard-line",
        aliases=["dl"],
        help="Remove specific lines from working tree",
        description="Remove ONLY the listed changed line IDs from working tree",
    )
    parser_discard_line.add_argument(
        "line_ids",
        help="Line IDs to discard (e.g., '1,3,5-7')",
    )
    parser_discard_line.set_defaults(func=lambda args: command_discard_line(args.line_ids))

    # include-file - Stage the entire file
    parser_include_file = subparsers.add_parser(
        "include-file",
        aliases=["if"],
        help="Stage the entire file containing the current hunk",
        description="Stage the entire file containing the current hunk to the index",
    )
    parser_include_file.set_defaults(func=lambda _: command_include_file())

    # skip-file - Skip the entire file
    parser_skip_file = subparsers.add_parser(
        "skip-file",
        aliases=["sf"],
        help="Skip all hunks in the current file",
        description="Skip all hunks in the file containing the current hunk",
    )
    parser_skip_file.set_defaults(func=lambda _: command_skip_file())

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
            print("No batch staging session in progress.", file=sys.stderr)
            print("Run 'git-stage-batch start' to begin.", file=sys.stderr)
            sys.exit(1)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
