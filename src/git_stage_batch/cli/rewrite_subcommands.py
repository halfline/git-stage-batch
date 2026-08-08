"""History-refinement subcommand registration."""

from __future__ import annotations

import argparse

from ..commands.rewrite_scan import command_rewrite_scan
from ..commands.rewrite_status import command_rewrite_status
from ..commands.rewrite_validate import command_rewrite_validate
from ..i18n import _
from .command_policy import (
    CommandPolicy,
    LockingPolicy,
    PagerPolicy,
    RepositoryPolicy,
    SessionOwnershipPolicy,
)
from .subcommand_parser import Subparsers, add_subcommand_parser


REWRITE_READ_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.ALLOW_FOREIGN,
    locking=LockingPolicy.NONE,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
)


def _dispatch_rewrite_scan(args: argparse.Namespace) -> None:
    command_rewrite_scan(
        args.boundary,
        output_path=args.output_path,
        porcelain=args.porcelain,
    )


def _dispatch_rewrite_validate(args: argparse.Namespace) -> None:
    command_rewrite_validate(args.plan_path, porcelain=args.porcelain)


def _dispatch_rewrite_status(args: argparse.Namespace) -> None:
    command_rewrite_status(porcelain=args.porcelain)


def add_rewrite_subcommand(subparsers: Subparsers) -> None:
    """Register deterministic rewrite commands."""
    parser_rewrite = add_subcommand_parser(
        subparsers,
        "rewrite",
        policy=REWRITE_READ_POLICY,
        help=_("Inspect a deterministic history refinement"),
    )
    actions = parser_rewrite.add_subparsers(
        dest="rewrite_action",
        required=True,
        help=_("Rewrite action"),
    )
    parser_scan = add_subcommand_parser(
        actions,
        "scan",
        policy=REWRITE_READ_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Capture exact history facts and a KEEP plan template"),
    )
    parser_scan.add_argument(
        "--output",
        dest="output_path",
        metavar="FILE",
        help=_("Atomically write the reusable JSON plan to FILE"),
    )
    parser_scan.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output the reusable JSON plan on standard output"),
    )
    parser_scan.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_(
            "Commit excluded from the scan (default: fork point with upstream)"
        ),
    )
    parser_scan.set_defaults(func=_dispatch_rewrite_scan)

    parser_validate = add_subcommand_parser(
        actions,
        "validate",
        policy=REWRITE_READ_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Validate a reviewed rewrite plan against live objects"),
    )
    parser_validate.add_argument(
        "plan_path",
        metavar="PLAN",
        help=_("Reusable rewrite plan emitted by rewrite scan"),
    )
    parser_validate.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output a machine-readable validation report"),
    )
    parser_validate.set_defaults(func=_dispatch_rewrite_validate)

    parser_status = add_subcommand_parser(
        actions,
        "status",
        policy=REWRITE_READ_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Inspect durable rewrite operation progress"),
    )
    parser_status.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output machine-readable operation state"),
    )
    parser_status.set_defaults(func=_dispatch_rewrite_status)
