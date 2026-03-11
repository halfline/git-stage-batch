"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse

from . import __version__
from .i18n import _


def main() -> None:
    """Main entry point for git-stage-batch."""
    parser = argparse.ArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )

    args = parser.parse_args()


if __name__ == "__main__":
    main()
