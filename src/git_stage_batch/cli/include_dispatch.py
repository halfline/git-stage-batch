"""CLI dispatch for the include command."""

from __future__ import annotations

import argparse
import shlex

from ..commands.file_scope.multi_file_actions import (
    include_each_resolved_file,
    run_for_each_resolved_file,
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
from ..exceptions import CommandError
from ..i18n import _
from .file_scope import resolve_batch_file_scope, resolve_live_file_scope
from .replacement_input import resolve_replacement_text


def _dispatch_include_replacement(args: argparse.Namespace) -> None:
    if args.as_text is not None and args.as_stdin:
        raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
    if args.line_ids and args.from_batch and not args.to_batch:
        if args.no_edge_overlap:
            raise CommandError(
                _(
                    "`--no-edge-overlap` only applies to live "
                    "`include --line --as` operations."
                )
            )
        resolved_batch_scope = resolve_batch_file_scope(
            args.from_batch,
            args.file,
            args.file_patterns,
        )
        resolved_file = resolved_batch_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
        replacement_text = resolve_replacement_text(args)
        command_include_from_batch(
            args.from_batch,
            args.line_ids,
            file=resolved_file,
            replacement_text=replacement_text,
        )
        return
    if args.line_ids and not args.from_batch and not args.to_batch:
        resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
        resolved_file = resolved_live_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
        replacement_text = resolve_replacement_text(args)
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
                _(
                    "`include --as` requires `--file` or `--line` "
                    "and does not support `--to`."
                )
            )
        if args.no_edge_overlap:
            raise CommandError(
                _("`--no-edge-overlap` requires `include --line --as`.")
            )
        if resolved_live_scope.is_multiple:
            raise CommandError(_("Cannot use --as with multiple files."))
        replacement_text = resolve_replacement_text(args)
        command_include_file_as(
            replacement_text,
            file=resolved_live_scope.optional_file(),
            auto_advance=args.auto_advance,
        )
        return
    raise CommandError(
        _(
            "`include --as` requires `--file` or `--line` "
            "and does not support `--to`."
        )
    )


def dispatch_include_command(args: argparse.Namespace) -> None:
    """Dispatch parsed include arguments."""
    replacement_requested = args.as_text is not None or args.as_stdin
    if replacement_requested:
        _dispatch_include_replacement(args)
        return
    if args.no_edge_overlap:
        raise CommandError(_("`--no-edge-overlap` requires `include --line --as`."))
    if args.from_batch:
        resolved_batch_scope = resolve_batch_file_scope(
            args.from_batch,
            args.file,
            args.file_patterns,
        )
        run_for_each_resolved_file(
            resolved_batch_scope,
            lambda file: command_include_from_batch(
                args.from_batch,
                args.line_ids,
                file,
            ),
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
        resolved_file = resolved_live_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
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
