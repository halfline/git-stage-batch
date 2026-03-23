"""CLI entry point."""

from __future__ import annotations

import sys

from ..exceptions import CommandError
from .argument_parser import parse_command_line
from .dispatch import dispatch_args


def main() -> None:
    """Main entry point for git-stage-batch."""
    try:
        args = parse_command_line(sys.argv[1:], quiet=False)
        if args is not None:
            dispatch_args(args)
    except CommandError as e:
        if e.message:
            print(e.message, file=sys.stderr)
        sys.exit(e.exit_code)
