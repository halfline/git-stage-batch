"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse
import subprocess

from . import __version__
from .i18n import _


class GitHelpArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that tries to use git help for --help."""

    def print_help(self, file=None):
        """Try to use git help, fall back to argparse help."""
        try:
            result = subprocess.run(
                ["git", "help", "stage-batch"],
                check=False,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return
        except (FileNotFoundError, OSError):
            pass

        # Fall back to standard argparse help
        super().print_help(file)


def main() -> None:
    """Main entry point for git-stage-batch."""
    parser = GitHelpArgumentParser(
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
