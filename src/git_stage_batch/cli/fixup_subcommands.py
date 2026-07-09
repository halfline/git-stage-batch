"""Fixup subcommand registration."""

from __future__ import annotations

from ..commands.suggest_fixup import (
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def _dispatch_suggest_fixup_command(args) -> None:
    if args.line_ids:
        command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last,
        )
        return

    command_suggest_fixup(
        args.boundary,
        reset=args.reset,
        abort=args.abort,
        show_last=args.last,
    )


def add_suggest_fixup_subcommand(subparsers) -> None:
    """Register the suggest-fixup subcommand."""
    parser_suggest_fixup = add_subcommand_parser(
        subparsers,
        "suggest-fixup",
        aliases=["x"],
        help=_("Suggest which commit the selected hunk should be fixed up to"),
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
        help=_(
            "Git ref to use as lower bound for commit search "
            "(default: @{upstream})"
        ),
    )
    parser_suggest_fixup.set_defaults(func=_dispatch_suggest_fixup_command)
