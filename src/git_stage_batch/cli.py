"""CLI entry point for git-stage-batch."""

from __future__ import annotations

import argparse

from . import __version__
from .commands import (
    command_again,
    command_block_file,
    command_discard,
    command_discard_line,
    command_exclude,
    command_exclude_file,
    command_exclude_line,
    command_include,
    command_include_file,
    command_include_line,
    command_show,
    command_start,
    command_status,
    command_stop,
    command_unblock_file,
)


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

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        help="Available commands",
    )

    # start - Find and display the first unprocessed hunk
    parser_start = subparsers.add_parser(
        "start",
        help="Find and display the first unprocessed hunk",
        description="Find and display the first unprocessed hunk; cache as 'current'",
    )
    parser_start.set_defaults(func=lambda _: command_start())

    # show - Reprint the cached current hunk
    parser_show = subparsers.add_parser(
        "show",
        help="Reprint the cached current hunk",
        description="Reprint the cached 'current' hunk (annotated with line IDs)",
    )
    parser_show.set_defaults(func=lambda _: command_show())

    # include - Stage the current hunk
    parser_include = subparsers.add_parser(
        "include",
        help="Stage the current hunk to the index",
        description="Stage the cached hunk (entire hunk) to the index; advance to next",
    )
    parser_include.set_defaults(func=lambda _: command_include())

    # exclude - Skip the current hunk
    parser_exclude = subparsers.add_parser(
        "exclude",
        help="Mark the current hunk as skipped",
        description="Mark the cached hunk as skipped; advance to next",
    )
    parser_exclude.set_defaults(func=lambda _: command_exclude())

    # discard - Remove the current hunk from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        help="Remove the current hunk from working tree",
        description="Reverse-apply the cached hunk to the working tree; advance to next",
    )
    parser_discard.set_defaults(func=lambda _: command_discard())

    # include-line - Stage specific lines
    parser_include_line = subparsers.add_parser(
        "include-line",
        help="Stage specific lines from the current hunk",
        description="Stage ONLY the listed changed line IDs (+/-) to the index",
    )
    parser_include_line.add_argument(
        "line_ids",
        help="Line IDs to include (e.g., '1,3,5-7')",
    )
    parser_include_line.set_defaults(func=lambda args: command_include_line(args.line_ids))

    # exclude-line - Skip specific lines
    parser_exclude_line = subparsers.add_parser(
        "exclude-line",
        help="Mark specific lines as skipped",
        description="Mark ONLY the listed changed line IDs as excluded (skip)",
    )
    parser_exclude_line.add_argument(
        "line_ids",
        help="Line IDs to exclude (e.g., '1,3,5-7')",
    )
    parser_exclude_line.set_defaults(func=lambda args: command_exclude_line(args.line_ids))

    # discard-line - Remove specific lines from working tree
    parser_discard_line = subparsers.add_parser(
        "discard-line",
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
        help="Stage the entire file containing the current hunk",
        description="Stage the entire file containing the current hunk to the index",
    )
    parser_include_file.set_defaults(func=lambda _: command_include_file())

    # exclude-file - Skip the entire file
    parser_exclude_file = subparsers.add_parser(
        "exclude-file",
        help="Skip all hunks in the current file",
        description="Skip all hunks in the file containing the current hunk",
    )
    parser_exclude_file.set_defaults(func=lambda _: command_exclude_file())

    # block-file - Permanently exclude a file
    parser_block_file = subparsers.add_parser(
        "block-file",
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

    # status - Show current state
    parser_status = subparsers.add_parser(
        "status",
        help="Show current state",
        description="Show brief state (current hunk summary, remaining line IDs)",
    )
    parser_status.set_defaults(func=lambda _: command_status())

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
