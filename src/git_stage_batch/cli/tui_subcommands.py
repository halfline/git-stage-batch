"""TUI subcommand registration."""

from __future__ import annotations

from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def add_interactive_subcommand(subparsers) -> None:
    """Register the interactive subcommand."""
    parser_interactive = add_subcommand_parser(
        subparsers,
        "interactive",
        help=_("Start interactive hunk-by-hunk mode"),
    )
    parser_interactive.set_defaults(interactive_command=True)
