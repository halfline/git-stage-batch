"""Fixup subcommand registration."""

from __future__ import annotations

import argparse

from ..commands.fixup_create import command_create_fixups
from ..commands.suggest_fixup import (
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from ..i18n import _
from .command_policy import (
    CommandPolicy,
    LockingPolicy,
    PagerPolicy,
    RepositoryPolicy,
    SessionOwnershipPolicy,
)
from .subcommand_parser import Subparsers, add_subcommand_parser


FIXUP_COMMAND_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.REQUIRE_AVAILABLE,
    locking=LockingPolicy.SESSION,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
)


def _dispatch_suggest_fixup_command(args: argparse.Namespace) -> None:
    if args.line_ids:
        command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last,
            porcelain=args.porcelain,
        )
        return

    command_suggest_fixup(
        args.boundary,
        reset=args.reset,
        abort=args.abort,
        show_last=args.last,
        porcelain=args.porcelain,
    )


def _add_suggest_fixup_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Analyze only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=_("Reset state and start search over from most recent"),
    )
    parser.add_argument(
        "--abort",
        action="store_true",
        help=_("Clear state and exit without showing candidates"),
    )
    parser.add_argument(
        "--last",
        action="store_true",
        help=_("Re-show the last candidate without advancing"),
    )
    parser.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output machine-readable JSON"),
    )
    parser.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_(
            "Git ref to use as lower bound for commit search (default: @{upstream})"
        ),
    )
    parser.set_defaults(func=_dispatch_suggest_fixup_command)


def _dispatch_create_fixups_command(args: argparse.Namespace) -> None:
    command_create_fixups(
        args.boundary,
        dry_run=args.dry_run,
        partial=args.partial,
        porcelain=args.porcelain,
    )


def _add_create_fixups_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=_("Show the complete assignment plan without creating commits"),
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help=_("Create eligible fixups and leave other units staged"),
    )
    parser.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output machine-readable JSON"),
    )
    parser.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_(
            "Git commit to use as the excluded base of the target range "
            "(default: fork point with upstream)"
        ),
    )
    parser.set_defaults(func=_dispatch_create_fixups_command)


def add_fixup_subcommand(subparsers: Subparsers) -> None:
    """Register the fixup command family."""
    parser_fixup = add_subcommand_parser(
        subparsers,
        "fixup",
        policy=FIXUP_COMMAND_POLICY,
        help=_("Suggest fixup targets or create grouped fixup commits"),
    )
    actions = parser_fixup.add_subparsers(
        dest="fixup_action",
        required=True,
        help=_("Fixup action"),
    )
    parser_suggest = add_subcommand_parser(
        actions,
        "suggest",
        policy=FIXUP_COMMAND_POLICY,
        help_topic="stage-batch-suggest-fixup",
        help=_("Suggest a target for the selected hunk"),
    )
    _add_suggest_fixup_arguments(parser_suggest)

    parser_create = add_subcommand_parser(
        actions,
        "create",
        policy=FIXUP_COMMAND_POLICY,
        help_topic="stage-batch-fixup",
        help=_("Create grouped fixup commits from staged changes"),
    )
    _add_create_fixups_arguments(parser_create)


def add_suggest_fixup_subcommand(subparsers: Subparsers) -> None:
    """Register the compatible suggest-fixup subcommand."""
    parser_suggest_fixup = add_subcommand_parser(
        subparsers,
        "suggest-fixup",
        policy=FIXUP_COMMAND_POLICY,
        aliases=["x"],
        help=_("Suggest which commit the selected hunk should be fixed up to"),
    )
    _add_suggest_fixup_arguments(parser_suggest_fixup)
