"""Session lifecycle subcommand registration."""

from __future__ import annotations

from ..commands.redo import command_redo
from ..commands.stop import command_stop
from ..commands.undo import command_undo
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


def add_undo_subcommand(subparsers) -> None:
    """Register the undo subcommand."""
    parser_undo = add_subcommand_parser(
        subparsers,
        "undo",
        aliases=["u", "back"],
        help=_("Undo the most recent undoable session operation"),
    )
    parser_undo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo checkpoint"),
    )
    parser_undo.set_defaults(func=lambda args: command_undo(force=args.force))


def add_redo_subcommand(subparsers) -> None:
    """Register the redo subcommand."""
    parser_redo = add_subcommand_parser(
        subparsers,
        "redo",
        aliases=["forward"],
        help=_("Redo the most recently undone session operation"),
    )
    parser_redo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo"),
    )
    parser_redo.set_defaults(func=lambda args: command_redo(force=args.force))
