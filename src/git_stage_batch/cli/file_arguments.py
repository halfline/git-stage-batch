"""Shared CLI file argument registration and normalization."""

from __future__ import annotations

import argparse

from ..i18n import _


def add_file_argument(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add a single-file argument that supports omitted values."""
    parser.add_argument(
        "--file",
        action="append",
        nargs="*",
        default=None,
        metavar="PATH",
        help=help_text,
    )
    parser.add_argument(
        "--files",
        dest="file_patterns",
        action="append",
        nargs="+",
        default=None,
        metavar="PATTERN",
        help=_("Resolve one or more gitignore-style PATTERNs to files."),
    )


def _flatten_file_patterns(
    pattern_groups: list[list[str]] | None,
) -> list[str] | None:
    """Flatten repeated --files groups into one ordered pattern list."""
    if pattern_groups is None:
        return None
    return [
        pattern
        for group in pattern_groups
        for pattern in group
    ]


def normalize_parsed_file_arguments(args: argparse.Namespace) -> None:
    """Normalize parser storage for --file/--files before dispatch."""
    if hasattr(args, "file_patterns"):
        args.file_patterns = _flatten_file_patterns(args.file_patterns)

    if not hasattr(args, "file") or args.file is None:
        return

    file_groups = args.file
    if file_groups and not file_groups[-1]:
        args.file = ""
        return

    file_values = [
        value
        for group in file_groups
        for value in group
    ]

    if len(file_values) == 1:
        args.file = file_values[0]
    else:
        args.file = file_values
