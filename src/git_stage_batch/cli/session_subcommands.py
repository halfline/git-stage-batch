"""Session lifecycle subcommand registration."""

from __future__ import annotations

from ..commands.stop import command_stop
from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def add_stop_subcommand(subparsers) -> None:
    """Register the stop subcommand."""
    parser_stop = add_subcommand_parser(
        subparsers,
        "stop",
        help=_("Stop the selected session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: command_stop())
