"""Root command-line parser construction."""

from __future__ import annotations

from .. import __version__
from ..i18n import _
from .git_help import GitHelpArgumentParser
from .subcommand_registry import add_cli_subcommands


def build_root_parser() -> GitHelpArgumentParser:
    """Build the top-level git-stage-batch argument parser."""
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
        help_topic="stage-batch",
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )
    parser.add_argument(
        "-C",
        dest="working_directory",
        metavar="path",
        default=None,
        help=_("Run as if started in path"),
    )
    parser.add_argument(
        "-i",
        dest="interactive_flag",
        action="store_true",
        help=_("Start interactive mode"),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help=_("Available commands"),
    )
    add_cli_subcommands(subparsers)

    return parser
