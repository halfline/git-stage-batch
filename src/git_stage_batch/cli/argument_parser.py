"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable

from .. import __version__
from ..batch.validation import batch_exists
from .. import commands
from ..batch.query import read_batch_metadata
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_patterns import list_changed_files, resolve_gitignore_style_patterns


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


def _add_file_argument(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add a single-file argument that supports omitted values."""
    parser.add_argument(
        "--file",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=help_text,
    )
    parser.add_argument(
        "--files",
        dest="file_patterns",
        nargs="+",
        default=None,
        metavar="PATTERN",
        help=_("Resolve one or more gitignore-style PATTERNs to files."),
    )


def _validate_file_inputs(
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> None:
    """Validate cross-argument rules for file-scoped operations."""
    if file_arg is not None and file_patterns is not None:
        raise CommandError(_("Cannot use --file together with --files."))


def _run_for_each_file(
    file_arg: str | list[str] | None,
    callback: Callable[[str | None], None],
    *,
    line_ids: str | None = None,
) -> None:
    """Run a callback once per resolved file argument."""
    if isinstance(file_arg, list) and line_ids is not None:
        raise CommandError(_("Cannot use --lines with multiple files."))
    if isinstance(file_arg, list):
        for file in file_arg:
            callback(file)
        return
    callback(file_arg)


def _resolve_live_file_scope(
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> str | list[str] | None:
    """Resolve single-file or pattern-based live file scope."""
    _validate_file_inputs(file_arg, file_patterns)
    if file_patterns is None:
        return file_arg

    resolved_files = resolve_gitignore_style_patterns(list_changed_files(), file_patterns)
    if not resolved_files:
        raise CommandError(
            _("No changed files matched: {patterns}").format(
                patterns=", ".join(file_patterns),
            )
        )
    if len(resolved_files) == 1:
        return resolved_files[0]
    return resolved_files


def _resolve_batch_file_scope(
    batch_name: str,
    file_arg: str | None,
    file_patterns: list[str] | None,
) -> str | list[str] | None:
    """Resolve single-file or pattern-based batch file scope."""
    _validate_file_inputs(file_arg, file_patterns)
    if file_patterns is None:
        return file_arg
    if not batch_exists(batch_name):
        raise CommandError(_("Batch '{name}' does not exist").format(name=batch_name))

    metadata = read_batch_metadata(batch_name)
    resolved_files = resolve_gitignore_style_patterns(metadata.get("files", {}).keys(), file_patterns)
    if not resolved_files:
        raise CommandError(
            _("No files in batch '{name}' matched: {patterns}").format(
                name=batch_name,
                patterns=", ".join(file_patterns),
            )
        )
    if len(resolved_files) == 1:
        return resolved_files[0]
    return resolved_files


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

    # interactive - Start interactive hunk-by-hunk mode
    parser_interactive = subparsers.add_parser(
        "interactive",
        help=_("Start interactive hunk-by-hunk mode"),
    )
    parser_interactive.set_defaults(func=lambda _: commands.command_interactive())

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

    # undo - Undo the most recent undoable session operation
    parser_undo = subparsers.add_parser(
        "undo",
        aliases=["u", "back"],
        help=_("Undo the most recent undoable session operation"),
    )
    parser_undo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo checkpoint"),
    )
    parser_undo.set_defaults(func=lambda args: commands.command_undo(force=args.force))

    # redo - Redo the most recently undone session operation
    parser_redo = subparsers.add_parser(
        "redo",
        aliases=["forward"],
        help=_("Redo the most recently undone session operation"),
    )
    parser_redo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo"),
    )
    parser_redo.set_defaults(func=lambda args: commands.command_redo(force=args.force))

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
    parser_show.add_argument(
        "--line",
        "--lines",
        dest="line_ids",
        metavar="IDS",
        help=_("Show only specific line IDs (e.g., '1,3,5-7')"),
    )
    _add_file_argument(
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
    def dispatch_show(args: argparse.Namespace) -> None:
        resolved_file_scope = (
            _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            if args.from_batch
            else _resolve_live_file_scope(args.file, args.file_patterns)
        )
        if args.from_batch:
            if isinstance(resolved_file_scope, list):
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                last_index = len(resolved_file_scope) - 1
                for index, file in enumerate(resolved_file_scope):
                    commands.command_show_from_batch(
                        args.from_batch,
                        args.line_ids,
                        file,
                        selectable=(index == last_index),
                    )
            else:
                commands.command_show_from_batch(args.from_batch, args.line_ids, resolved_file_scope)
            return
        if args.line_ids or resolved_file_scope is not None:
            if isinstance(resolved_file_scope, list) and args.porcelain:
                raise CommandError(_("Cannot use --porcelain with multiple files."))
            if isinstance(resolved_file_scope, list):
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                last_index = len(resolved_file_scope) - 1
                for index, file in enumerate(resolved_file_scope):
                    commands.command_show(
                        file=file,
                        porcelain=args.porcelain,
                        selectable=(index == last_index),
                    )
            else:
                commands.command_show(file=resolved_file_scope, porcelain=args.porcelain)
            return
        commands.command_show(porcelain=args.porcelain)

    parser_show.set_defaults(func=dispatch_show)

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
    _add_file_argument(
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
        help=_("Replace selected lines with TEXT before staging them"),
    )

    def dispatch_include(args: argparse.Namespace) -> None:
        resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
        resolved_batch_scope = (
            _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            if args.from_batch else None
        )
        if args.as_text is not None:
            if args.line_ids and args.from_batch and not args.to_batch:
                if isinstance(resolved_batch_scope, list):
                    raise CommandError(_("Cannot use --lines with multiple files."))
                commands.command_include_from_batch(
                    args.from_batch,
                    args.line_ids,
                    file=resolved_batch_scope,
                    replacement_text=args.as_text,
                )
                return
            if args.line_ids and not args.from_batch and not args.to_batch:
                if isinstance(resolved_live_scope, list):
                    raise CommandError(_("Cannot use --lines with multiple files."))
                commands.command_include_line_as(args.line_ids, args.as_text, file=resolved_live_scope)
                return
            raise CommandError(
                _("`include --as` requires `--line` and does not support `--to`.")
            )
        if args.from_batch:
            _run_for_each_file(
                resolved_batch_scope,
                lambda file: commands.command_include_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
            )
        elif args.to_batch:
            _run_for_each_file(
                resolved_live_scope,
                lambda file: commands.command_include_to_batch(args.to_batch, args.line_ids, file),
                line_ids=args.line_ids,
            )
        elif args.line_ids:
            if isinstance(resolved_live_scope, list):
                raise CommandError(_("Cannot use --lines with multiple files."))
            commands.command_include_line(args.line_ids, file=resolved_live_scope)
        elif resolved_live_scope is not None:
            _run_for_each_file(resolved_live_scope, commands.command_include_file)
        else:
            commands.command_include()

    parser_include.set_defaults(func=dispatch_include)

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
    _add_file_argument(
        parser_skip,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, skips all hunks from the file."),
    )

    def dispatch_skip(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_live_file_scope(args.file, args.file_patterns)
        if args.line_ids:
            if isinstance(resolved_file_scope, list):
                raise CommandError(_("Cannot use --lines with multiple files."))
            commands.command_skip_line(args.line_ids)
        elif resolved_file_scope is not None:
            _run_for_each_file(resolved_file_scope, commands.command_skip_file)
        else:
            commands.command_skip()

    parser_skip.set_defaults(func=dispatch_skip)

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
    _add_file_argument(
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
        help=_("Replace selected lines with TEXT before saving them to batch"),
    )

    def dispatch_discard(args: argparse.Namespace) -> None:
        resolved_live_scope = _resolve_live_file_scope(args.file, args.file_patterns)
        resolved_batch_scope = (
            _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            if args.from_batch else None
        )
        if args.as_text is not None:
            if args.to_batch and args.line_ids and not args.from_batch:
                if isinstance(resolved_live_scope, list):
                    raise CommandError(_("Cannot use --lines with multiple files."))
                commands.command_discard_line_as_to_batch(
                    args.to_batch,
                    args.line_ids,
                    args.as_text,
                    file=resolved_live_scope,
                )
                return
            raise CommandError(
                _("`discard --as` requires `--to` and `--line`.")
            )
        if args.from_batch:
            _run_for_each_file(
                resolved_batch_scope,
                lambda file: commands.command_discard_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
            )
        elif args.to_batch:
            _run_for_each_file(
                resolved_live_scope,
                lambda file: commands.command_discard_to_batch(args.to_batch, args.line_ids, file),
                line_ids=args.line_ids,
            )
        elif args.line_ids:
            if isinstance(resolved_live_scope, list):
                raise CommandError(_("Cannot use --lines with multiple files."))
            commands.command_discard_line(args.line_ids, file=resolved_live_scope)
        elif resolved_live_scope is not None:
            _run_for_each_file(resolved_live_scope, commands.command_discard_file)
        else:
            commands.command_discard()

    parser_discard.set_defaults(func=dispatch_discard)

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
    _add_file_argument(
        parser_apply,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses first file in batch (sorted order). "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_apply(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        _run_for_each_file(
            resolved_file_scope,
            lambda file: commands.command_apply_from_batch(
                args.from_batch,
                line_ids=args.line_ids if hasattr(args, "line_ids") else None,
                file=file,
            ),
            line_ids=args.line_ids if hasattr(args, "line_ids") else None,
        )

    parser_apply.set_defaults(func=dispatch_apply)

    # reset - Remove claims from batch
    parser_reset = subparsers.add_parser(
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
    _add_file_argument(
        parser_reset,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_reset(args: argparse.Namespace) -> None:
        resolved_file_scope = _resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        _run_for_each_file(
            resolved_file_scope,
            lambda file: commands.command_reset_from_batch(
                args.from_batch,
                args.line_ids,
                file,
                None,
                args.to_batch,
            ),
            line_ids=args.line_ids,
        )

    parser_reset.set_defaults(func=dispatch_reset)

    # sift - Reconcile batch against current tip
    parser_sift = subparsers.add_parser(
        "sift",
        help=_("Remove already-present portions from a batch"),
    )
    parser_sift.add_argument(
        "--from",
        dest="from_batch",
        metavar="BATCH",
        required=True,
        help=_("Source batch to sift"),
    )
    parser_sift.add_argument(
        "--to",
        dest="to_batch",
        metavar="BATCH",
        required=True,
        help=_("Destination batch (may equal source for in-place sift)"),
    )
    parser_sift.set_defaults(func=lambda args: commands.command_sift_batch(args.from_batch, args.to_batch))

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
