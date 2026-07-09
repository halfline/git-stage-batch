"""Selected-change subcommand registration."""

from __future__ import annotations

import argparse

from ..i18n import _
from .auto_advance_options import add_auto_advance_arguments
from .discard_dispatch import dispatch_discard_command
from .file_arguments import add_file_argument
from .include_dispatch import dispatch_include_command
from .show_dispatch import dispatch_show_command
from .skip_dispatch import dispatch_skip_command
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


def add_include_subcommand(subparsers) -> None:
    """Register the include subcommand."""
    parser_include = add_subcommand_parser(
        subparsers,
        "include",
        aliases=["i"],
        help=_("Stage the selected hunk"),
    )
    parser_include.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Stage only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_include,
        _(
            "Operate on entire file (live working tree state). "
            "If PATH omitted, uses selected hunk's file. "
            "Without --line, stages entire file. "
            "With --line, operates on line IDs from entire file."
        ),
    )
    parser_include.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Include changes from batch"),
    )
    parser_include.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Include changes to batch"),
    )
    parser_include.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_(
            "Replace selected lines, or full file with --file, "
            "using TEXT before staging"
        ),
    )
    parser_include.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_(
            "Read replacement text from standard input exactly, "
            "preserving trailing newlines"
        ),
    )
    parser_include.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_(
            "Do not strip unchanged edge-overlap lines from replacement "
            "text used with --as"
        ),
    )
    parser_include.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    add_auto_advance_arguments(parser_include)
    parser_include.set_defaults(func=dispatch_include_command)


def add_skip_subcommand(subparsers) -> None:
    """Register the skip subcommand."""
    parser_skip = add_subcommand_parser(
        subparsers,
        "skip",
        aliases=["s"],
        help=_("Skip the selected hunk without staging"),
    )
    parser_skip.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Skip only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_skip,
        _(
            "Operate on entire file (live working tree state). "
            "If PATH omitted, uses selected hunk's file. "
            "Without --line, skips all hunks from the file."
        ),
    )
    add_auto_advance_arguments(parser_skip)
    parser_skip.set_defaults(func=dispatch_skip_command)


def add_discard_subcommand(subparsers) -> None:
    """Register the discard subcommand."""
    parser_discard = add_subcommand_parser(
        subparsers,
        "discard",
        aliases=["d"],
        help=_("Remove the selected hunk from working tree"),
    )
    parser_discard.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Discard only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_discard,
        _(
            "Operate on entire file (live working tree state). "
            "If PATH omitted, uses selected hunk's file. "
            "Without --line, discards entire file. "
            "With --line, operates on line IDs from entire file."
        ),
    )
    parser_discard.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Discard changes from batch"),
    )
    parser_discard.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Discard changes to batch"),
    )
    parser_discard.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Replace selected lines, or full file with --file, using TEXT"),
    )
    parser_discard.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_(
            "Read replacement text from standard input exactly, "
            "preserving trailing newlines"
        ),
    )
    parser_discard.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_(
            "Do not strip unchanged edge-overlap lines from replacement "
            "text used with --as"
        ),
    )
    parser_discard.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    add_auto_advance_arguments(parser_discard)
    parser_discard.set_defaults(func=dispatch_discard_command)
