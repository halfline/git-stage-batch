"""CLI entry point."""

from __future__ import annotations

import sys

from .argument_parser import parse_command_line
from .dispatch import dispatch_args


def main() -> None:
    """Main entry point for git-stage-batch."""
    args = parse_command_line(sys.argv[1:], quiet=False)
    if args is not None:
        dispatch_args(args)
