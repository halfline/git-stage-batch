"""Command-line argument parsing."""

from __future__ import annotations

import argparse
import shlex
import sys

from .. import __version__
from ..batch.query import read_batch_metadata
from ..batch.source_selector import batch_name_for_source_lookup
from ..batch.validation import batch_exists
from ..commands.abort import command_abort
from ..commands.again import command_again
from ..commands.annotate import command_annotate_batch
from ..commands.apply_from import command_apply_from_batch
from ..commands.block_file import command_block_file
from ..commands.check_unstaged import command_check_unstaged
from ..commands.discard import (
    command_discard,
    command_discard_file,
    command_discard_file_as,
    command_discard_line,
    command_discard_line_as_to_batch,
    command_discard_to_batch,
)
from ..commands.discard_from import command_discard_from_batch
from ..commands.drop import command_drop_batch
from ..core.replacement import ReplacementText
from ..commands.file_scope.multi_file_actions import (
    discard_to_batch_each_resolved_file,
    include_each_resolved_file,
    run_for_each_resolved_file,
    skip_each_resolved_file,
)
from ..commands.include import (
    command_include,
    command_include_file,
    command_include_file_as,
    command_include_line,
    command_include_line_as,
    command_include_to_batch,
)
from ..commands.include_from import command_include_from_batch
from ..commands.install_assets import command_install_assets
from ..commands.list import command_list_batches
from ..commands.new import command_new_batch
from ..commands.redo import command_redo
from ..commands.reset import command_reset_from_batch
from ..commands.show import command_show, command_show_file_list
from ..commands.show_from import command_show_from_batch
from ..commands.sift import command_sift_batch
from ..commands.skip import command_skip, command_skip_file, command_skip_line
from ..commands.start import command_start
from ..commands.status import command_status
from ..commands.stop import command_stop
from ..commands.suggest_fixup import (
    command_suggest_fixup,
    command_suggest_fixup_line,
)
from ..commands.unblock_file import command_unblock_file
from ..commands.undo import command_undo
from ..exceptions import CommandError
from ..i18n import _
from ..output.status_prompt import DEFAULT_PROMPT_FORMAT
from .completion import command_complete_files
from .file_scope import (
    FileArgument,
    resolve_batch_file_scope,
    resolve_live_file_scope,
)
from .git_help import GitHelpArgumentParser


def _add_subcommand_parser(
    subparsers,
    command_name: str,
    **kwargs,
) -> GitHelpArgumentParser:
    """Add a subcommand parser wired to its git help topic."""
    help_topic = kwargs.pop("help_topic", f"stage-batch-{command_name}")
    return subparsers.add_parser(
        command_name,
        help_topic=help_topic,
        **kwargs,
    )


def _add_file_argument(parser: argparse.ArgumentParser, help_text: str) -> None:
    """Add a single-file argument that supports omitted values."""
    parser.add_argument(
        "--file",
        action="append",
        nargs="*",
        default=None,
        metavar="PATH",
        help=help_text,
    )
    parser.add_argument(
        "--files",
        dest="file_patterns",
        action="append",
        nargs="+",
        default=None,
        metavar="PATTERN",
        help=_("Resolve one or more gitignore-style PATTERNs to files."),
    )


def _add_auto_advance_arguments(parser: argparse.ArgumentParser) -> None:
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


def _flatten_file_patterns(
    pattern_groups: list[list[str]] | None,
) -> list[str] | None:
    """Flatten repeated --files groups into one ordered pattern list."""
    if pattern_groups is None:
        return None
    return [
        pattern
        for group in pattern_groups
        for pattern in group
    ]


def _normalize_parsed_file_arguments(args: argparse.Namespace) -> None:
    """Normalize parser storage for --file/--files before dispatch."""
    if hasattr(args, "file_patterns"):
        args.file_patterns = _flatten_file_patterns(args.file_patterns)

    if not hasattr(args, "file") or args.file is None:
        return

    file_groups = args.file
    if file_groups and not file_groups[-1]:
        args.file = ""
        return

    file_values = [
        value
        for group in file_groups
        for value in group
    ]

    if len(file_values) == 1:
        args.file = file_values[0]
    else:
        args.file = file_values


def _resolve_replacement_text(args: argparse.Namespace) -> str | None:
    """Return replacement text from `--as` or exact stdin content."""
    if getattr(args, "as_text", None) is not None and getattr(args, "as_stdin", False):
        raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
    if getattr(args, "as_stdin", False):
        data = sys.stdin.buffer.read()
        return ReplacementText(
            data.decode("utf-8", errors="surrogateescape"),
            data=data,
            exact=True,
        )
    as_text = getattr(args, "as_text", None)
    if as_text is not None:
        return ReplacementText(as_text, exact=True)
    return None


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

    # check-unstaged - Check whether the index fits an unstaged-only workflow
    parser_check_unstaged = _add_subcommand_parser(
        subparsers,
        "check-unstaged",
        help=_("Check whether the index fits an unstaged-only workflow"),
    )
    parser_check_unstaged.set_defaults(func=lambda _: command_check_unstaged())

    # start - Start a new batch staging session
    parser_start = _add_subcommand_parser(
        subparsers,
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
    _add_auto_advance_arguments(parser_start)
    parser_start.set_defaults(
        func=lambda args: command_start(
            context_lines=args.context_lines,
            auto_advance=args.auto_advance,
        )
    )

    # interactive - Start interactive hunk-by-hunk mode
    parser_interactive = _add_subcommand_parser(
        subparsers,
        "interactive",
        help=_("Start interactive hunk-by-hunk mode"),
    )
    parser_interactive.set_defaults(interactive_command=True)

    # stop - Stop the selected session and clear state
    parser_stop = _add_subcommand_parser(
        subparsers,
        "stop",
        help=_("Stop the selected session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: command_stop())

    # again - Clear state and start a fresh pass
    parser_again = _add_subcommand_parser(
        subparsers,
        "again",
        aliases=["a"],
        help=_("Clear state and start a fresh pass"),
    )
    _add_auto_advance_arguments(parser_again)
    parser_again.set_defaults(
        func=lambda args: command_again(auto_advance=args.auto_advance)
    )

    # undo - Undo the most recent undoable session operation
    parser_undo = _add_subcommand_parser(
        subparsers,
        "undo",
        aliases=["u", "back"],
        help=_("Undo the most recent undoable session operation"),
    )
    parser_undo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo checkpoint"),
    )
    parser_undo.set_defaults(func=lambda args: command_undo(force=args.force))

    # redo - Redo the most recently undone session operation
    parser_redo = _add_subcommand_parser(
        subparsers,
        "redo",
        aliases=["forward"],
        help=_("Redo the most recently undone session operation"),
    )
    parser_redo.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite changes made after the undo"),
    )
    parser_redo.set_defaults(func=lambda args: command_redo(force=args.force))

    # show - Show the selected hunk
    parser_show = _add_subcommand_parser(
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
    def dispatch_show(args: argparse.Namespace) -> None:
        replacement_requested = args.as_text is not None or args.as_stdin
        if replacement_requested and not args.from_batch:
            raise CommandError(_("`show --as` requires `--from`."))
        if args.as_text is not None and args.as_stdin:
            raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
        resolved_file_scope = (
            resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            if args.from_batch
            else resolve_live_file_scope(args.file, args.file_patterns)
        )
        if args.page is not None:
            lookup_batch = batch_name_for_source_lookup(args.from_batch) if args.from_batch else None
            if lookup_batch and not batch_exists(lookup_batch):
                raise CommandError(_("Batch '{name}' does not exist").format(name=lookup_batch))
            if resolved_file_scope.is_implicit:
                if not (
                    args.from_batch
                    and lookup_batch is not None
                    and batch_exists(lookup_batch)
                    and len(read_batch_metadata(lookup_batch).get("files", {})) == 1
                ):
                    raise CommandError(
                        _(
                            "`show --page` requires `--file` or a single-file `--files` match, "
                            "unless `--from` names a single-file batch."
                        )
                    )
            if resolved_file_scope.is_multiple:
                raise CommandError(_("`show --page` requires exactly one resolved file."))
            if args.line_ids is not None:
                raise CommandError(_("Cannot use `show --page` together with `show --line`."))
            if args.porcelain:
                raise CommandError(_("Cannot use `show --page` with `--porcelain`."))
        if args.from_batch:
            replacement_text = _resolve_replacement_text(args) if replacement_requested else None
            show_kwargs = {"page": args.page}
            if args.porcelain:
                show_kwargs["porcelain"] = args.porcelain
            if not args.advance:
                show_kwargs["selectable"] = False
            if replacement_text is not None:
                show_kwargs["replacement_text"] = replacement_text
            if resolved_file_scope.is_multiple:
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                if replacement_requested:
                    raise CommandError(_("`show --as` requires exactly one resolved file."))
                command_show_from_batch(
                    args.from_batch,
                    args.line_ids,
                    patterns=args.file_patterns,
                    **show_kwargs,
                )
            else:
                command_show_from_batch(
                    args.from_batch,
                    args.line_ids,
                    resolved_file_scope.optional_file(),
                    **show_kwargs,
                )
            return
        if args.line_ids or not resolved_file_scope.is_implicit:
            if resolved_file_scope.is_multiple and args.porcelain:
                raise CommandError(_("Cannot use --porcelain with multiple files."))
            if resolved_file_scope.is_multiple:
                if args.line_ids:
                    raise CommandError(_("Cannot use --lines with multiple files."))
                show_list_kwargs = {}
                if not args.advance:
                    show_list_kwargs["selectable"] = False
                command_show_file_list(
                    list(resolved_file_scope.files),
                    **show_list_kwargs,
                )
            else:
                show_kwargs = {
                    "file": resolved_file_scope.optional_file(),
                    "page": args.page,
                    "porcelain": args.porcelain,
                }
                if not args.advance:
                    show_kwargs["selectable"] = False
                command_show(
                    **show_kwargs,
                )
            return
        show_kwargs = {"porcelain": args.porcelain}
        if not args.advance:
            show_kwargs["selectable"] = False
        command_show(**show_kwargs)

    parser_show.set_defaults(func=dispatch_show)

    # status - Show selected session status
    parser_status = _add_subcommand_parser(
        subparsers,
        "status",
        aliases=["st"],
        help=_("Show selected session status"),
    )
    status_output = parser_status.add_mutually_exclusive_group()
    status_output.add_argument(
        "--porcelain",
        action="store_true",
        help=_("Output JSON for scripting instead of human-readable text"),
    )
    status_output.add_argument(
        "--for-prompt",
        dest="prompt_format",
        nargs="?",
        const=DEFAULT_PROMPT_FORMAT,
        metavar="FORMAT",
        help=_("Print FORMAT only when a session is active, for shell prompts"),
    )
    parser_status.set_defaults(
        func=lambda args: command_status(
            porcelain=args.porcelain,
            prompt_format=args.prompt_format,
        )
    )

    # include - Stage the selected hunk
    parser_include = _add_subcommand_parser(
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
    _add_auto_advance_arguments(parser_include)

    def dispatch_include(args: argparse.Namespace) -> None:
        replacement_requested = args.as_text is not None or args.as_stdin
        if replacement_requested:
            if args.as_text is not None and args.as_stdin:
                raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
            if args.line_ids and args.from_batch and not args.to_batch:
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` only applies to live `include --line --as` operations."))
                resolved_batch_scope = resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
                resolved_file = resolved_batch_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                command_include_from_batch(
                    args.from_batch,
                    args.line_ids,
                    file=resolved_file,
                    replacement_text=replacement_text,
                )
                return
            if args.line_ids and not args.from_batch and not args.to_batch:
                resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
                resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                command_include_line_as(
                    args.line_ids,
                    replacement_text,
                    file=resolved_file,
                    no_edge_overlap=args.no_edge_overlap,
                    auto_advance=args.auto_advance,
                )
                return
            if (
                args.line_ids is None
                and args.from_batch is None
                and args.to_batch is None
            ):
                resolved_live_scope = resolve_live_file_scope(
                    args.file,
                    args.file_patterns,
                    include_staged=True,
                )
                if resolved_live_scope.is_implicit:
                    raise CommandError(
                        _("`include --as` requires `--file` or `--line` and does not support `--to`.")
                    )
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` requires `include --line --as`."))
                if resolved_live_scope.is_multiple:
                    raise CommandError(_("Cannot use --as with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                command_include_file_as(
                    replacement_text,
                    file=resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
                return
            raise CommandError(
                _("`include --as` requires `--file` or `--line` and does not support `--to`.")
            )
        if args.no_edge_overlap:
            raise CommandError(_("`--no-edge-overlap` requires `include --line --as`."))
        if args.from_batch:
            resolved_batch_scope = resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            run_for_each_resolved_file(
                resolved_batch_scope,
                lambda file: command_include_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
                undo_operation=f"include --from {shlex.quote(args.from_batch)}",
                worktree_paths=resolved_batch_scope.files,
            )
        elif args.to_batch:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            run_for_each_resolved_file(
                resolved_live_scope,
                lambda file: command_include_to_batch(
                    args.to_batch,
                    args.line_ids,
                    file,
                    auto_advance=args.auto_advance,
                ),
                line_ids=args.line_ids,
                undo_operation=f"include --to {shlex.quote(args.to_batch)}",
            )
        elif args.line_ids:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
            command_include_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        else:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            if resolved_live_scope.is_multiple:
                include_each_resolved_file(
                    list(resolved_live_scope.files),
                    auto_advance=args.auto_advance,
                )
            elif not resolved_live_scope.is_implicit:
                command_include_file(
                    resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
            else:
                command_include(auto_advance=args.auto_advance)

    parser_include.set_defaults(func=dispatch_include)

    # skip - Skip the selected hunk without staging
    parser_skip = _add_subcommand_parser(
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
    _add_file_argument(
        parser_skip,
        _("Operate on entire file (live working tree state). "
          "If PATH omitted, uses selected hunk's file. "
          "Without --line, skips all hunks from the file."),
    )
    _add_auto_advance_arguments(parser_skip)

    def dispatch_skip(args: argparse.Namespace) -> None:
        resolved_file_scope = resolve_live_file_scope(args.file, args.file_patterns)
        if args.line_ids:
            resolved_file = resolved_file_scope.require_single_file(_("Cannot use --lines with multiple files."))
            command_skip_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        elif not resolved_file_scope.is_implicit:
            if resolved_file_scope.is_multiple:
                skip_each_resolved_file(
                    list(resolved_file_scope.files),
                    auto_advance=args.auto_advance,
                )
            else:
                command_skip_file(
                    resolved_file_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
        else:
            command_skip(auto_advance=args.auto_advance)

    parser_skip.set_defaults(func=dispatch_skip)

    # discard - Remove the selected hunk from working tree
    parser_discard = _add_subcommand_parser(
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
    _add_auto_advance_arguments(parser_discard)

    def dispatch_discard(args: argparse.Namespace) -> None:
        replacement_requested = args.as_text is not None or args.as_stdin
        if replacement_requested:
            if args.as_text is not None and args.as_stdin:
                raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
            if args.to_batch and args.line_ids and not args.from_batch:
                resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
                resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                command_discard_line_as_to_batch(
                    args.to_batch,
                    args.line_ids,
                    replacement_text,
                    file=resolved_file,
                    no_edge_overlap=args.no_edge_overlap,
                    auto_advance=args.auto_advance,
                )
                return
            if (
                args.to_batch is None
                and args.from_batch is None
                and args.line_ids is None
            ):
                resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
                if resolved_live_scope.is_implicit:
                    raise CommandError(
                        _("`discard --as` requires `--file`, or `--to` with `--line`.")
                    )
                if args.no_edge_overlap:
                    raise CommandError(_("`--no-edge-overlap` requires `discard --to --line --as`."))
                if resolved_live_scope.is_multiple:
                    raise CommandError(_("Cannot use --as with multiple files."))
                replacement_text = _resolve_replacement_text(args)
                command_discard_file_as(
                    replacement_text,
                    file=resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
                return
            raise CommandError(
                _("`discard --as` requires `--file`, or `--to` with `--line`.")
            )
        if args.no_edge_overlap:
            raise CommandError(_("`--no-edge-overlap` requires `discard --to --line --as`."))
        if args.from_batch:
            resolved_batch_scope = resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
            run_for_each_resolved_file(
                resolved_batch_scope,
                lambda file: command_discard_from_batch(args.from_batch, args.line_ids, file),
                line_ids=args.line_ids,
                undo_operation=f"discard --from {shlex.quote(args.from_batch)}",
                worktree_paths=resolved_batch_scope.files,
            )
        elif args.to_batch:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            if resolved_live_scope.is_multiple and args.line_ids is None:
                discard_to_batch_each_resolved_file(
                    args.to_batch,
                    list(resolved_live_scope.files),
                    auto_advance=args.auto_advance,
                )
            else:
                run_for_each_resolved_file(
                    resolved_live_scope,
                    lambda file: command_discard_to_batch(
                        args.to_batch,
                        args.line_ids,
                        file,
                        auto_advance=args.auto_advance,
                    ),
                    line_ids=args.line_ids,
                    undo_operation=f"discard --to {shlex.quote(args.to_batch)}",
                    worktree_paths=resolved_live_scope.files,
                )
        elif args.line_ids:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            resolved_file = resolved_live_scope.require_single_file(_("Cannot use --lines with multiple files."))
            command_discard_line(
                args.line_ids,
                file=resolved_file,
                auto_advance=args.auto_advance,
            )
        else:
            resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
            if not resolved_live_scope.is_implicit:
                run_for_each_resolved_file(
                    resolved_live_scope,
                    lambda file: command_discard_file(
                        file,
                        auto_advance=args.auto_advance,
                    ),
                    undo_operation="discard",
                    worktree_paths=resolved_live_scope.files,
                )
            else:
                command_discard(auto_advance=args.auto_advance)

    parser_discard.set_defaults(func=dispatch_discard)

    # abort - Restore repository to pre-session state
    parser_abort = _add_subcommand_parser(
        subparsers,
        "abort",
        help=_("Restore repository to pre-session state"),
    )
    parser_abort.set_defaults(func=lambda _: command_abort())

    # block-file - Permanently exclude a file
    parser_block_file = _add_subcommand_parser(
        subparsers,
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
    parser_block_file.add_argument(
        "--local-only",
        action="store_true",
        default=False,
        help=_("Add to .git/info/exclude instead of .gitignore"),
    )
    parser_block_file.set_defaults(func=lambda args: command_block_file(args.file_path, local_only=args.local_only))

    # unblock-file - Remove a file from blocked list
    parser_unblock_file = _add_subcommand_parser(
        subparsers,
        "unblock-file",
        aliases=["ubf"],
        help=_("Remove a file from the blocked list"),
    )
    parser_unblock_file.add_argument(
        "file_path",
        help=_("Path to the file to unblock"),
    )
    parser_unblock_file.set_defaults(func=lambda args: command_unblock_file(args.file_path))

    # suggest-fixup - Suggest which commit the selected hunk should be fixed up to
    parser_suggest_fixup = _add_subcommand_parser(
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

    # new - Create a new batch
    parser_new = _add_subcommand_parser(
        subparsers,
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
    parser_new.set_defaults(func=lambda args: command_new_batch(args.batch_name, args.note))

    # list - List all batches
    parser_list = _add_subcommand_parser(
        subparsers,
        "list",
        help=_("List all batches"),
    )
    parser_list.set_defaults(func=lambda _: command_list_batches())

    # drop - Delete a batch
    parser_drop = _add_subcommand_parser(
        subparsers,
        "drop",
        help=_("Delete a batch"),
    )
    parser_drop.add_argument(
        "batch_name",
        help=_("Name of the batch to delete"),
    )
    parser_drop.set_defaults(func=lambda args: command_drop_batch(args.batch_name))

    # annotate - Add/update batch description
    parser_annotate = _add_subcommand_parser(
        subparsers,
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
    parser_annotate.set_defaults(func=lambda args: command_annotate_batch(args.batch_name, args.note))

    # apply - Apply batch changes to working tree
    parser_apply = _add_subcommand_parser(
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
    _add_file_argument(
        parser_apply,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses first file in batch (sorted order). "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_apply(args: argparse.Namespace) -> None:
        resolved_file_scope = resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        run_for_each_resolved_file(
            resolved_file_scope,
            lambda file: command_apply_from_batch(
                args.from_batch,
                line_ids=args.line_ids if hasattr(args, "line_ids") else None,
                file=file,
            ),
            line_ids=args.line_ids if hasattr(args, "line_ids") else None,
            undo_operation=f"apply --from {shlex.quote(args.from_batch)}",
            worktree_paths=resolved_file_scope.files,
        )

    parser_apply.set_defaults(func=dispatch_apply)

    # reset - Remove claims from batch
    parser_reset = _add_subcommand_parser(
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
    _add_file_argument(
        parser_reset,
        _("Operate on entire file from batch. "
          "If PATH omitted, uses selected hunk's file. "
          "With --line, operates on line IDs from entire file."),
    )

    def dispatch_reset(args: argparse.Namespace) -> None:
        resolved_file_scope = resolve_batch_file_scope(args.from_batch, args.file, args.file_patterns)
        command_parts = ["reset", "--from", shlex.quote(args.from_batch)]
        if args.to_batch is not None:
            command_parts.extend(["--to", shlex.quote(args.to_batch)])
        if args.line_ids is not None:
            command_parts.extend(["--line", shlex.quote(args.line_ids)])
        run_for_each_resolved_file(
            resolved_file_scope,
            lambda file: command_reset_from_batch(
                args.from_batch,
                args.line_ids,
                file,
                None,
                args.to_batch,
            ),
            line_ids=args.line_ids,
            undo_operation=" ".join(command_parts),
        )

    parser_reset.set_defaults(func=dispatch_reset)

    # sift - Reconcile batch against current tip
    parser_sift = _add_subcommand_parser(
        subparsers,
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
    parser_sift.set_defaults(func=lambda args: command_sift_batch(args.from_batch, args.to_batch))

    parser_install_assets = _add_subcommand_parser(
        subparsers,
        "install-assets",
        help=_("Install bundled assistant assets into the repository"),
    )
    parser_install_assets.add_argument(
        "asset_group",
        choices=["claude-agents", "claude-skills", "codex-skills"],
        nargs="?",
        help=_("Bundled asset group to install"),
    )
    parser_install_assets.add_argument(
        "--filter",
        dest="filters",
        metavar="PATTERN",
        nargs="+",
        help=_("Install only bundled assets whose names match one or more gitignore-style PATTERNs"),
    )
    parser_install_assets.add_argument(
        "--force",
        action="store_true",
        help=_("Overwrite an existing installed asset"),
    )
    parser_install_assets.set_defaults(
        func=lambda args: command_install_assets(
            args.asset_group,
            args.filters,
            force=args.force,
        )
    )

    parser_complete_files = subparsers.add_parser(
        "__complete-files",
        help=argparse.SUPPRESS,
    )
    parser_complete_files.add_argument(
        "current_token",
        nargs="?",
        default="",
    )
    parser_complete_files.add_argument(
        "--from",
        dest="from_batch",
        default=None,
    )
    parser_complete_files.set_defaults(
        func=lambda args: command_complete_files(args.current_token, from_batch=args.from_batch)
    )

    # Parse arguments, return None on failure
    try:
        parsed_args = parser.parse_args(expanded)
        _normalize_parsed_file_arguments(parsed_args)
        return parsed_args
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None
    except SystemExit as e:
        if quiet and e.code != 0:
            return None
        raise
