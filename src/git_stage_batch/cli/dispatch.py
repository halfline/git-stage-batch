"""Command dispatch logic."""

from __future__ import annotations

import argparse


def dispatch_args(args: argparse.Namespace) -> None:
    """Execute the command based on parsed arguments.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    # Currently no subcommands, just version
    pass
