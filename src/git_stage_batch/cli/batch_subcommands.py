"""Batch-management subcommand registration."""

from __future__ import annotations

from ..commands.annotate import command_annotate_batch
from ..commands.drop import command_drop_batch
from ..commands.list import command_list_batches
from ..commands.new import command_new_batch
from ..commands.sift import command_sift_batch
from ..i18n import _
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
