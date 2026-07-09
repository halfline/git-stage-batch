"""Shared CLI options for action auto-advance behavior."""

from __future__ import annotations

import argparse

from ..i18n import _


def add_auto_advance_arguments(parser: argparse.ArgumentParser) -> None:
    """Add controls for selecting the next hunk after an action."""
    auto_advance = parser.add_mutually_exclusive_group()
    auto_advance.add_argument(
        "--auto-advance",
        dest="auto_advance",
        action="store_true",
        default=None,
        help=_("Select the next hunk after the command completes"),
    )
    auto_advance.add_argument(
        "--no-auto-advance",
        dest="auto_advance",
        action="store_false",
        help=_("Leave no hunk selected after the command completes"),
    )
