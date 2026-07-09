"""Session lifecycle subcommand registration."""

from __future__ import annotations

from ..commands.abort import command_abort
from ..commands.check_unstaged import command_check_unstaged
from ..commands.redo import command_redo
from ..commands.status import command_status
from ..commands.stop import command_stop
from ..commands.undo import command_undo
from ..i18n import _
from ..output.status_prompt import DEFAULT_PROMPT_FORMAT
from .subcommand_parser import add_subcommand_parser


def add_check_unstaged_subcommand(subparsers) -> None:
    """Register the check-unstaged subcommand."""
    parser_check_unstaged = add_subcommand_parser(
        subparsers,
        "check-unstaged",
        help=_("Check whether the index fits an unstaged-only workflow"),
    )
    parser_check_unstaged.set_defaults(func=lambda _: command_check_unstaged())


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


def add_abort_subcommand(subparsers) -> None:
    """Register the abort subcommand."""
    parser_abort = add_subcommand_parser(
        subparsers,
        "abort",
        help=_("Restore repository to pre-session state"),
    )
    parser_abort.set_defaults(func=lambda _: command_abort())


def add_status_subcommand(subparsers) -> None:
    """Register the status subcommand."""
    parser_status = add_subcommand_parser(
        subparsers,
        "status",
        aliases=["st"],
        help=_("Show selected session status"),
    )
    status_output = parser_status.add_mutually_exclusive_group()
    status_output.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    status_output.add_argument(
        "--for-prompt",
        dest="prompt_format",
        nargs="?",
        const=DEFAULT_PROMPT_FORMAT,
        metavar="FORMAT",
        help=_("Print FORMAT only when a session is active, for shell prompts"),
    )
    parser_status.set_defaults(
        func=lambda args: command_status(
            porcelain=args.porcelain,
            prompt_format=args.prompt_format,
        )
    )
