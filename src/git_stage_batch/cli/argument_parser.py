"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import subprocess
import sys

from .. import __version__
from .. import commands
from ..i18n import _


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


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    # Mapping from shortcuts to their expanded forms
    quick_actions = {
        '?': ['--help'],
        'if': ['include', '--file'],
        'il': ['include', '--line'],
        'sf': ['skip', '--file'],
        'sl': ['skip', '--line'],
        'df': ['discard', '--file'],
        'dl': ['discard', '--line'],
    }

    # Expand quick actions
    expanded = []
    for arg in args:
        if arg in quick_actions:
            expanded.extend(quick_actions[arg])
        else:
            expanded.append(arg)

    # Create parser
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
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

    subparsers = parser.add_subparsers(
        dest="command",
        help=_("Available commands"),
    )

    # start - Start a new batch staging session
    parser_start = subparsers.add_parser(
        "start",
        help=_("Start a new batch staging session"),
    )
    parser_start.add_argument(
        "-U",
        "--unified",
        dest="context_lines",
        type=int,
        metavar="N",
        help=_("Number of context lines in diff output (default: 3)"),
    )
    parser_start.set_defaults(func=lambda args: commands.command_start(context_lines=args.context_lines))

    # stop - Stop the selected session and clear state
    parser_stop = subparsers.add_parser(
        "stop",
        help=_("Stop the selected session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: commands.command_stop())

    # again - Clear state and start a fresh pass
    parser_again = subparsers.add_parser(
        "again",
        aliases=["a"],
        help=_("Clear state and start a fresh pass"),
    )
    parser_again.set_defaults(func=lambda _: commands.command_again())

    # show - Show the selected hunk
    parser_show = subparsers.add_parser(
        "show",
        help=_("Show the selected hunk"),
    )
    parser_show.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        help=_("Show changes from batch"),
    )
    parser_show.set_defaults(func=lambda args: (
        commands.command_show_from_batch(args.from_batch) if args.from_batch
        else commands.command_show()
    ))

    # status - Show selected session status
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help=_("Show selected session status"),
    )
    parser_status.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    parser_status.set_defaults(func=lambda args: commands.command_status(porcelain=args.porcelain))

    # include - Stage the selected hunk
    parser_include = subparsers.add_parser(
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
    parser_include.add_argument(
        "--file",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=_("Operate on entire file (live working tree state). "
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
    parser_include.set_defaults(func=lambda args: (
        commands.command_include_from_batch(args.from_batch, args.line_ids, args.file) if args.from_batch
        else commands.command_include_to_batch(args.to_batch, args.line_ids, args.file) if args.to_batch
        else commands.command_include_line(args.line_ids) if args.line_ids
        else commands.command_include_file(args.file) if args.file is not None
        else commands.command_include()
    ))

    # skip - Skip the selected hunk without staging
    parser_skip = subparsers.add_parser(
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
    parser_skip.add_argument(
        "--file",
        action="store_true",
        help=_("Skip all hunks from the selected file"),
    )
    parser_skip.set_defaults(func=lambda args: (
        commands.command_skip_line(args.line_ids) if args.line_ids
        else commands.command_skip_file() if args.file
        else commands.command_skip()
    ))

    # discard - Remove the selected hunk from working tree
    parser_discard = subparsers.add_parser(
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
    parser_discard.add_argument(
        "--file",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=_("Operate on entire file (live working tree state). "
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
    parser_discard.set_defaults(func=lambda args: (
        commands.command_discard_from_batch(args.from_batch, args.line_ids, args.file) if args.from_batch
        else commands.command_discard_to_batch(args.to_batch, args.line_ids, args.file) if args.to_batch
        else commands.command_discard_line(args.line_ids) if args.line_ids
        else commands.command_discard_file(args.file) if args.file is not None
        else commands.command_discard()
    ))

    # abort - Restore repository to pre-session state
    parser_abort = subparsers.add_parser(
        "abort",
        help=_("Restore repository to pre-session state"),
    )
    parser_abort.set_defaults(func=lambda _: commands.command_abort())

    # block-file - Permanently exclude a file
    parser_block_file = subparsers.add_parser(
        "block-file",
        aliases=["bf"],
        help=_("Permanently exclude a file (adds to .gitignore)"),
    )
    parser_block_file.add_argument(
        "file_path",
        nargs="?",
        default="",
        help=_("Path to the file to block (defaults to selected hunk's file)"),
    )
    parser_block_file.set_defaults(func=lambda args: commands.command_block_file(args.file_path))

    # unblock-file - Remove a file from blocked list
    parser_unblock_file = subparsers.add_parser(
        "unblock-file",
        aliases=["ubf"],
        help=_("Remove a file from the blocked list"),
    )
    parser_unblock_file.add_argument(
        "file_path",
        help=_("Path to the file to unblock"),
    )
    parser_unblock_file.set_defaults(func=lambda args: commands.command_unblock_file(args.file_path))

    # suggest-fixup - Suggest which commit the selected hunk should be fixed up to
    parser_suggest_fixup = subparsers.add_parser(
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
        commands.command_suggest_fixup_line(
            args.line_ids,
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        ) if args.line_ids else
        commands.command_suggest_fixup(
            args.boundary,
            reset=args.reset,
            abort=args.abort,
            show_last=args.last
        )
    ))

    # new - Create a new batch
    parser_new = subparsers.add_parser(
        "new",
        help=_("Create a new batch"),
    )
    parser_new.add_argument(
        "batch_name",
        help=_("Name of the batch to create"),
    )
    parser_new.add_argument(
        "-m", "--note",
        default="",
        help=_("Optional description for the batch"),
    )
    parser_new.set_defaults(func=lambda args: commands.command_new_batch(args.batch_name, args.note))

    # list - List all batches
    parser_list = subparsers.add_parser(
        "list",
        help=_("List all batches"),
    )
    parser_list.set_defaults(func=lambda _: commands.command_list_batches())

    # drop - Delete a batch
    parser_drop = subparsers.add_parser(
        "drop",
        help=_("Delete a batch"),
    )
    parser_drop.add_argument(
        "batch_name",
        help=_("Name of the batch to delete"),
    )
    parser_drop.set_defaults(func=lambda args: commands.command_drop_batch(args.batch_name))

    # annotate - Add/update batch description
    parser_annotate = subparsers.add_parser(
        "annotate",
        help=_("Add or update batch description"),
    )
    parser_annotate.add_argument(
        "batch_name",
        help=_("Name of the batch"),
    )
    parser_annotate.add_argument(
        "note",
        help=_("Description text"),
    )
    parser_annotate.set_defaults(func=lambda args: commands.command_annotate_batch(args.batch_name, args.note))

    # apply - Apply batch changes to working tree
    parser_apply = subparsers.add_parser(
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
    parser_apply.add_argument(
        "--file",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=_("Operate on entire file from batch. "
               "If PATH omitted, uses first file in batch (sorted order). "
               "With --line, operates on line IDs from entire file."),
    )
    parser_apply.set_defaults(func=lambda args: commands.command_apply_from_batch(args.from_batch, line_ids=args.line_ids if hasattr(args, 'line_ids') else None, file=args.file if hasattr(args, 'file') else None))

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
