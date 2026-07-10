"""Batch subcommand registration."""

from __future__ import annotations

from ..commands.annotate import command_annotate_batch
from ..commands.drop import command_drop_batch
from ..commands.list import command_list_batches
from ..commands.new import command_new_batch
from ..commands.sift import command_sift_batch
from ..i18n import _
from .apply_dispatch import dispatch_apply_command
from .file_arguments import add_file_argument
from .reset_dispatch import dispatch_reset_command
from .subcommand_parser import add_subcommand_parser


def add_new_subcommand(subparsers) -> None:
    """Register the new subcommand."""
    parser_new = add_subcommand_parser(
        subparsers,
        "new",
        help=_("Create a new batch"),
    )
    parser_new.add_argument(
        "batch_name",
        help=_("Name of the batch to create"),
    )
    parser_new.add_argument(
        "-m",
        "--note",
        default="",
        help=_("Optional description for the batch"),
    )
    parser_new.set_defaults(
        func=lambda args: command_new_batch(args.batch_name, args.note)
    )


def add_list_subcommand(subparsers) -> None:
    """Register the list subcommand."""
    parser_list = add_subcommand_parser(
        subparsers,
        "list",
        help=_("List all batches"),
    )
    parser_list.set_defaults(func=lambda _: command_list_batches())


def add_drop_subcommand(subparsers) -> None:
    """Register the drop subcommand."""
    parser_drop = add_subcommand_parser(
        subparsers,
        "drop",
        help=_("Delete a batch"),
    )
    parser_drop.add_argument(
        "batch_name",
        help=_("Name of the batch to delete"),
    )
    parser_drop.set_defaults(func=lambda args: command_drop_batch(args.batch_name))


def add_annotate_subcommand(subparsers) -> None:
    """Register the annotate subcommand."""
    parser_annotate = add_subcommand_parser(
        subparsers,
        "annotate",
        help=_("Add or update batch description"),
    )
    parser_annotate.add_argument(
        "batch_name",
        help=_("Name of the batch"),
    )
    parser_annotate.add_argument(
        "note",
        help=_("Description text"),
    )
    parser_annotate.set_defaults(
        func=lambda args: command_annotate_batch(args.batch_name, args.note)
    )


def add_sift_subcommand(subparsers) -> None:
    """Register the sift subcommand."""
    parser_sift = add_subcommand_parser(
        subparsers,
        "sift",
        help=_("Remove already-present portions from a batch"),
    )
    parser_sift.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Source batch to sift"),
    )
    parser_sift.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        required=True,
        help=_("Destination batch (may equal source for in-place sift)"),
    )
    parser_sift.set_defaults(
        func=lambda args: command_sift_batch(args.from_batch, args.to_batch)
    )


def add_apply_subcommand(subparsers) -> None:
    """Register the apply subcommand."""
    parser_apply = add_subcommand_parser(
        subparsers,
        "apply",
        help=_("Apply batch changes to working tree"),
    )
    parser_apply.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Apply changes from batch to working tree"),
    )
    parser_apply.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Apply only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_apply,
        _(
            "Operate on entire file from batch. "
            "If PATH omitted, uses first file in batch (sorted order). "
            "With --line, operates on line IDs from entire file."
        ),
    )
    parser_apply.set_defaults(func=dispatch_apply_command)


def add_reset_subcommand(subparsers) -> None:
    """Register the reset subcommand."""
    parser_reset = add_subcommand_parser(
        subparsers,
        "reset",
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Move reset claims to another batch"),
    )
    parser_reset.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Reset only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_reset,
        _(
            "Operate on entire file from batch. "
            "If PATH omitted, uses selected hunk's file. "
            "With --line, operates on line IDs from entire file."
        ),
    )
    parser_reset.set_defaults(func=dispatch_reset_command)
