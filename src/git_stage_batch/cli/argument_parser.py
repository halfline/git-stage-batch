"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import sys

from .. import __version__
from ..commands.suggest_fixup import (
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from ..i18n import _
from .apply_dispatch import dispatch_apply_command
from .asset_subcommands import add_install_assets_subcommand
from .auto_advance_options import add_auto_advance_arguments
from .batch_subcommands import (
    add_annotate_subcommand,
    add_drop_subcommand,
    add_list_subcommand,
    add_new_subcommand,
    add_sift_subcommand,
)
from .completion import add_completion_subcommand
from .discard_dispatch import dispatch_discard_command
from .file_blocking_subcommands import (
    add_block_file_subcommand,
    add_unblock_file_subcommand,
)
from .file_arguments import add_file_argument, normalize_parsed_file_arguments
from .file_scope import (
    FileArgument,
)
from .git_help import GitHelpArgumentParser
from .include_dispatch import dispatch_include_command
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
from .show_dispatch import dispatch_show_command
from .skip_dispatch import dispatch_skip_command
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

    # show - Show the selected hunk
    parser_show = add_subcommand_parser(
        subparsers,
        "show",
        help=_("Show the selected hunk"),
    )
    parser_show.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Show changes from batch"),
    )
    parser_show.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Show only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_show.add_argument(
        "--page",
        "--pages",
        metavar="PAGES",
        dest="page",
        help=_("Show page selection for a file review, e.g. '3', '3-5', '1,3,5-7', or 'all'."),
    )
    add_file_argument(
        parser_show,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_show.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    parser_show.add_argument(
        "--no-advance",
        dest="advance",
        action="store_false",
        default=True,
        help=_("Preview without selecting the shown change for later actions"),
    )
    parser_show.add_argument(
        "--no-auto-advance",
        dest="advance",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser_show.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Preview selected batch lines as replacement text"),
    )
    parser_show.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement preview text from standard input exactly"),
    )
    parser_show.set_defaults(func=dispatch_show_command)

    add_status_subcommand(subparsers)

    # include - Stage the selected hunk
    parser_include = add_subcommand_parser(
        subparsers,
        "include",
        aliases=["i"],
        help=_("Stage the selected hunk"),
    )
    parser_include.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Stage only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_include,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, stages entire file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_include.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Include changes from batch"),
    )
    parser_include.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Include changes to batch"),
    )
    parser_include.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Replace selected lines, or full file with --file, using TEXT before staging"),
    )
    parser_include.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement text from standard input exactly, preserving trailing newlines"),
    )
    parser_include.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_("Do not strip unchanged edge-overlap lines from replacement text used with --as"),
    )
    parser_include.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    add_auto_advance_arguments(parser_include)

    parser_include.set_defaults(func=dispatch_include_command)

    # skip - Skip the selected hunk without staging
    parser_skip = add_subcommand_parser(
        subparsers,
        "skip",
        aliases=["s"],
        help=_("Skip the selected hunk without staging"),
    )
    parser_skip.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Skip only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_skip,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, skips all hunks from the file."),
    )
    add_auto_advance_arguments(parser_skip)

    parser_skip.set_defaults(func=dispatch_skip_command)

    # discard - Remove the selected hunk from working tree
    parser_discard = add_subcommand_parser(
        subparsers,
        "discard",
        aliases=["d"],
        help=_("Remove the selected hunk from working tree"),
    )
    parser_discard.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Discard only specific line IDs (e.g., '1,3,5-7')"),
    )
    add_file_argument(
        parser_discard,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, discards entire file. "
          "With --line, operates on line IDs from entire file."),
    )
    parser_discard.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Discard changes from batch"),
    )
    parser_discard.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        help=_("Discard changes to batch"),
    )
    parser_discard.add_argument(
        "--as",
        dest="as_text",
        metavar="TEXT",
        help=_("Replace selected lines, or full file with --file, using TEXT"),
    )
    parser_discard.add_argument(
        "--as-stdin",
        dest="as_stdin",
        action="store_true",
        help=_("Read replacement text from standard input exactly, preserving trailing newlines"),
    )
    parser_discard.add_argument(
        "--no-edge-overlap",
        dest="no_edge_overlap",
        action="store_true",
        help=_("Do not strip unchanged edge-overlap lines from replacement text used with --as"),
    )
    parser_discard.add_argument(
        "--no-anchor",
        dest="no_edge_overlap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    add_auto_advance_arguments(parser_discard)

    parser_discard.set_defaults(func=dispatch_discard_command)

    add_abort_subcommand(subparsers)

    add_block_file_subcommand(subparsers)

    add_unblock_file_subcommand(subparsers)

    # suggest-fixup - Suggest which commit the selected hunk should be fixed up to
    parser_suggest_fixup = add_subcommand_parser(
        subparsers,
        "suggest-fixup",
        aliases=["x"],
        help=_("Suggest which commit the selected hunk should be fixed up to"),
    )
    parser_suggest_fixup.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Analyze only specific line IDs (e.g., '1,3,5-7')"),
    )
    parser_suggest_fixup.add_argument(
        "--reset",
        action="store_true",
        help=_("Reset state and start search over from most recent"),
    )
    parser_suggest_fixup.add_argument(
        "--abort",
        action="store_true",
        help=_("Clear state and exit without showing candidates"),
    )
    parser_suggest_fixup.add_argument(
        "--last",
        action="store_true",
        help=_("Re-show the last candidate without advancing"),
    )
    parser_suggest_fixup.add_argument(
        "boundary",
        nargs="?",
        default=None,
        help=_("Git ref to use as lower bound for commit search (default: @{upstream})"),
    )
    parser_suggest_fixup.set_defaults(func=lambda args: (
        command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        ) if args.line_ids else
        command_suggest_fixup(
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        )
    ))

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
