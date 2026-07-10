"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import sys

from .file_arguments import normalize_parsed_file_arguments
from .quick_actions import expand_quick_actions
from .root_parser import build_root_parser


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    expanded = expand_quick_actions(args)
    parser = build_root_parser()

    # Parse arguments, return None on failure
    try:
        parsed_args = parser.parse_args(expanded)
        normalize_parsed_file_arguments(parsed_args)
        return parsed_args
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
    except SystemExit as e:
        if quiet and e.code != 0:
            return None
        raise
