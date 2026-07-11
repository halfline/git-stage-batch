"""Diagnostic journal subcommand registration."""

from __future__ import annotations

from ..commands.journal import command_journal
from ..i18n import _
from .subcommand_parser import add_subcommand_parser


def add_journal_subcommand(subparsers) -> None:
    """Register the diagnostic journal management command."""
    parser = add_subcommand_parser(
        subparsers,
        "journal",
        help=_("Inspect or purge diagnostic journal data"),
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--path",
        action="store_true",
        help=_("Print the journal path"),
    )
    action.add_argument(
        "--purge",
        action="store_true",
        help=_("Delete journal data for this repository"),
    )
    parser.add_argument(
        "--all",
        dest="all_repositories",
        action="store_true",
        help=_("With --purge, delete journal data for all repositories"),
    )
    parser.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output stable JSON"),
    )
    parser.set_defaults(
        func=lambda args: command_journal(
            path_only=args.path,
            purge=args.purge,
            all_repositories=args.all_repositories,
            porcelain=args.porcelain,
        )
    )
