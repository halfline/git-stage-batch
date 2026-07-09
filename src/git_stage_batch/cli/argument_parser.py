"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..i18n import _
from .apply_dispatch import dispatch_apply_command
from .asset_subcommands import add_install_assets_subcommand
from .batch_subcommands import (
    add_annotate_subcommand,
    add_drop_subcommand,
    add_list_subcommand,
    add_new_subcommand,
    add_sift_subcommand,
)
from .completion import add_completion_subcommand
from .file_blocking_subcommands import (
    add_block_file_subcommand,
    add_unblock_file_subcommand,
)
from .file_arguments import add_file_argument, normalize_parsed_file_arguments
from .file_scope import (
    FileArgument,
)
from .fixup_subcommands import add_suggest_fixup_subcommand
from .git_help import GitHelpArgumentParser
from .quick_actions import expand_quick_actions
from .reset_dispatch import dispatch_reset_command
from .session_subcommands import (
    add_abort_subcommand,
    add_again_subcommand,
    add_check_unstaged_subcommand,
    add_redo_subcommand,
    add_start_subcommand,
    add_status_subcommand,
    add_stop_subcommand,
    add_undo_subcommand,
)
from .selection_subcommands import (
    add_discard_subcommand,
    add_include_subcommand,
    add_show_subcommand,
    add_skip_subcommand,
)
from .subcommand_parser import add_subcommand_parser


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    expanded = expand_quick_actions(args)

    # Create parser
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

    add_check_unstaged_subcommand(subparsers)

    add_start_subcommand(subparsers)

    # interactive - Start interactive hunk-by-hunk mode
    parser_interactive = add_subcommand_parser(
        subparsers,
        "interactive",
        help=_("Start interactive hunk-by-hunk mode"),
    )
    parser_interactive.set_defaults(interactive_command=True)

    add_stop_subcommand(subparsers)

    add_again_subcommand(subparsers)

    add_undo_subcommand(subparsers)

    add_redo_subcommand(subparsers)

    add_show_subcommand(subparsers)

    add_status_subcommand(subparsers)

    add_include_subcommand(subparsers)

    add_skip_subcommand(subparsers)

    add_discard_subcommand(subparsers)

    add_abort_subcommand(subparsers)

    add_block_file_subcommand(subparsers)

    add_unblock_file_subcommand(subparsers)

    add_suggest_fixup_subcommand(subparsers)

    add_new_subcommand(subparsers)

    add_list_subcommand(subparsers)

    add_drop_subcommand(subparsers)

    add_annotate_subcommand(subparsers)

    # apply - Apply batch changes to working tree
    parser_apply = add_subcommand_parser(
        subparsers,
        "apply",
        help=_("Apply batch changes to working tree"),
    )
    parser_apply.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Apply changes from batch to working tree"),
    )
    parser_apply.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Apply only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_apply,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses first file in batch (sorted order). "
          "With --line, operates on line IDs from entire file."),
    )

    parser_apply.set_defaults(func=dispatch_apply_command)

    # reset - Remove claims from batch
    parser_reset = add_subcommand_parser(
        subparsers,
        "reset",
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Remove claims from batch"),
    )
    parser_reset.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Move reset claims to another batch"),
    )
    parser_reset.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Reset only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_reset,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )

    parser_reset.set_defaults(func=dispatch_reset_command)

    add_sift_subcommand(subparsers)

    add_install_assets_subcommand(subparsers)

    add_completion_subcommand(subparsers)

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
