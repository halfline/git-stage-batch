"""History-refinement subcommand registration."""

from __future__ import annotations

import argparse

from ..commands.rewrite_abort import command_rewrite_abort
from ..commands.rewrite_apply import command_rewrite_apply
from ..commands.rewrite_continue import command_rewrite_continue
from ..commands.rewrite_lint import command_rewrite_lint
from ..commands.rewrite_resolve import command_rewrite_resolve
from ..commands.rewrite_scan import command_rewrite_scan
from ..commands.rewrite_status import command_rewrite_status
from ..commands.rewrite_validate import command_rewrite_validate
from ..commands.rewrite_verify import command_rewrite_verify
from ..exceptions import CommandError
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
REWRITE_OFFLINE_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.ALLOW_FOREIGN,
    locking=LockingPolicy.NONE,
    repository=RepositoryPolicy.OPTIONAL,
    pager=PagerPolicy.NEVER,
)
REWRITE_MUTATION_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.REQUIRE_AVAILABLE,
    locking=LockingPolicy.SESSION,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
)
REWRITE_VERIFICATION_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.ALLOW_FOREIGN,
    locking=LockingPolicy.SESSION,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
)


def _dispatch_rewrite_scan(args: argparse.Namespace) -> None:
    if args.onto is not None and args.boundary is None:
        raise CommandError(
            _("rewrite scan --onto requires an explicit movable base argument")
        )
    command_rewrite_scan(
        args.boundary,
        onto_boundary=args.onto,
        output_path=args.output_path,
        porcelain=args.porcelain,
    )


def _dispatch_rewrite_validate(args: argparse.Namespace) -> None:
    command_rewrite_validate(
        args.plan_path,
        resolutions_path=args.resolutions_path,
        porcelain=args.porcelain,
    )


def _dispatch_rewrite_lint(args: argparse.Namespace) -> None:
    command_rewrite_lint(args.plan_path, porcelain=args.porcelain)


def _dispatch_rewrite_resolve(args: argparse.Namespace) -> None:
    command_rewrite_resolve(
        args.plan_path,
        workspace_path=args.workspace_path,
        accept_result=args.accept_result,
        porcelain=args.porcelain,
    )


def _dispatch_rewrite_status(args: argparse.Namespace) -> None:
    command_rewrite_status(porcelain=args.porcelain)


def _dispatch_rewrite_apply(args: argparse.Namespace) -> None:
    command_rewrite_apply(
        args.plan_path,
        resolutions_path=args.resolutions_path,
        allowed_remote_refs=tuple(args.allowed_remote_refs),
        porcelain=args.porcelain,
    )


def _dispatch_rewrite_continue(args: argparse.Namespace) -> None:
    command_rewrite_continue(porcelain=args.porcelain)


def _dispatch_rewrite_abort(args: argparse.Namespace) -> None:
    command_rewrite_abort(porcelain=args.porcelain)


def _dispatch_rewrite_verify(args: argparse.Namespace) -> None:
    command_rewrite_verify(porcelain=args.porcelain)


def add_rewrite_subcommand(subparsers: Subparsers) -> None:
    """Register deterministic rewrite commands."""
    parser_rewrite = add_subcommand_parser(
        subparsers,
        "rewrite",
        policy=REWRITE_READ_POLICY,
        help=_("Plan, execute, and inspect a deterministic history refinement"),
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
        "--onto",
        metavar="ONTO",
        default=None,
        help=_(
            "Older frozen base captured by the scan; movable commits may "
            "integrate into it but it is never split or reordered (default: "
            "the movable base)"
        ),
    )
    parser_scan.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_(
            "Movable base: commit excluded from the scan whose successors may "
            "move (default: fork point with upstream)"
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
        "--workspace",
        dest="resolutions_path",
        metavar="DIR",
        help=_("Completed private rewrite-resolution workspace"),
    )
    parser_validate.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output a machine-readable validation report"),
    )
    parser_validate.set_defaults(func=_dispatch_rewrite_validate)

    parser_lint = add_subcommand_parser(
        actions,
        "lint",
        policy=REWRITE_OFFLINE_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Advisory-check a frozen rewrite plan without Git or replay"),
    )
    parser_lint.add_argument(
        "plan_path",
        metavar="PLAN",
        help=_("Reusable rewrite plan emitted by rewrite scan"),
    )
    parser_lint.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output all findings as a machine-readable report"),
    )
    parser_lint.set_defaults(func=_dispatch_rewrite_lint)

    parser_resolve = add_subcommand_parser(
        actions,
        "resolve",
        policy=REWRITE_READ_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Create or advance an external rewrite-resolution workspace"),
    )
    parser_resolve.add_argument(
        "plan_path",
        metavar="PLAN",
        help=_("Reviewed rewrite plan containing resolved outputs"),
    )
    parser_resolve.add_argument(
        "--workspace",
        dest="workspace_path",
        required=True,
        metavar="DIR",
        help=_("Private external directory for resolution artifacts"),
    )
    parser_resolve.add_argument(
        "--accept",
        dest="accept_result",
        action="store_true",
        help=_("Import the current result and advance to the next output"),
    )
    parser_resolve.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output a machine-readable resolution checkpoint"),
    )
    parser_resolve.set_defaults(func=_dispatch_rewrite_resolve)

    parser_apply = add_subcommand_parser(
        actions,
        "apply",
        policy=REWRITE_MUTATION_POLICY,
        help_topic="stage-batch-rewrite",
        help=_("Build, verify, and atomically update the checked-out branch"),
    )
    parser_apply.add_argument(
        "plan_path",
        metavar="PLAN",
        help=_("Validated reusable rewrite plan"),
    )
    parser_apply.add_argument(
        "--workspace",
        dest="resolutions_path",
        metavar="DIR",
        help=_("Completed private rewrite-resolution workspace"),
    )
    parser_apply.add_argument(
        "--allow-published-ref",
        dest="allowed_remote_refs",
        action="append",
        default=[],
        metavar="REF",
        help=_("Allow local rewriting of commits contained by remote REF"),
    )
    parser_apply.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output a machine-readable operation result"),
    )
    parser_apply.set_defaults(func=_dispatch_rewrite_apply)

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

    for action, help_text, dispatcher, policy in (
        (
            "continue",
            _("Resume the exact action in the active rewrite checkpoint"),
            _dispatch_rewrite_continue,
            REWRITE_MUTATION_POLICY,
        ),
        (
            "abort",
            _("Abort and conditionally restore the original branch tip"),
            _dispatch_rewrite_abort,
            REWRITE_MUTATION_POLICY,
        ),
        (
            "verify",
            _("Independently verify the active or latest rewrite output"),
            _dispatch_rewrite_verify,
            REWRITE_VERIFICATION_POLICY,
        ),
    ):
        parser_action = add_subcommand_parser(
            actions,
            action,
            policy=policy,
            help_topic="stage-batch-rewrite",
            help=help_text,
        )
        parser_action.add_argument(
            "--porcelain",
            action="store_true",
            help=_("Output a machine-readable operation result"),
        )
        parser_action.set_defaults(func=dispatcher)
