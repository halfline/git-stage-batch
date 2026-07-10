"""File blocking subcommand registration."""

from __future__ import annotations

from ..commands.block_file import command_block_file
from ..commands.unblock_file import command_unblock_file
from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def add_block_file_subcommand(subparsers) -> None:
    """Register the block-file subcommand."""
    parser_block_file = add_subcommand_parser(
        subparsers,
        "block-file",
        aliases=["bf"],
        help=_("Permanently exclude a file (adds to .gitignore)"),
    )
    parser_block_file.add_argument(
        "file_path",
        nargs="?",
        default="",
        help=_("Path to the file to block (defaults to selected hunk's file)"),
    )
    parser_block_file.add_argument(
        "--local-only",
        action="store_true",
        default=False,
        help=_("Add to .git/info/exclude instead of .gitignore"),
    )
    parser_block_file.set_defaults(
        func=lambda args: command_block_file(
            args.file_path,
            local_only=args.local_only,
        )
    )


def add_unblock_file_subcommand(subparsers) -> None:
    """Register the unblock-file subcommand."""
    parser_unblock_file = add_subcommand_parser(
        subparsers,
        "unblock-file",
        aliases=["ubf"],
        help=_("Remove a file from the blocked list"),
    )
    parser_unblock_file.add_argument(
        "file_path",
        help=_("Path to the file to unblock"),
    )
    parser_unblock_file.set_defaults(
        func=lambda args: command_unblock_file(args.file_path)
    )
