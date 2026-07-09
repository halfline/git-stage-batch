"""Selected-change subcommand registration."""

from __future__ import annotations

import argparse

from ..i18n import _
from .file_arguments import add_file_argument
from .show_dispatch import dispatch_show_command
from .subcommand_parser import add_subcommand_parser


def add_show_subcommand(subparsers) -> None:
    """Register the show subcommand."""
    parser_show = add_subcommand_parser(
        subparsers,
        "show",
        help=_("Show the selected hunk"),
    )
    parser_show.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Show changes from batch"),
    )
    parser_show.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Show only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_show.add_argument(
        "--page",
        "--pages",
        metavar="PAGES",
        dest="page",
        help=_(
            "Show page selection for a file review, e.g. '3', "
            "'3-5', '1,3,5-7', or 'all'."
        ),
    )
    add_file_argument(
        parser_show,
        _(
            "Operate on entire file (live working tree state). "
            "If PATH omitted, uses selected hunk's file. "
            "With --line, operates on line IDs from entire file."
        ),
    )
    parser_show.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    parser_show.add_argument(
        "--no-advance",
        dest="advance",
        action="store_false",
        default=True,
        help=_("Preview without selecting the shown change for later actions"),
    )
    parser_show.add_argument(
        "--no-auto-advance",
        dest="advance",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser_show.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Preview selected batch lines as replacement text"),
    )
    parser_show.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement preview text from standard input exactly"),
    )
    parser_show.set_defaults(func=dispatch_show_command)
