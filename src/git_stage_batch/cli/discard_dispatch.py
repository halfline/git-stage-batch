"""CLI dispatch for the discard command."""

from __future__ import annotations

import argparse
import shlex

from ..commands.discard import (
    command_discard,
    command_discard_file,
    command_discard_file_as,
    command_discard_line,
    command_discard_line_as_to_batch,
    command_discard_to_batch,
)
from ..commands.discard_from import command_discard_from_batch
from ..commands.file_scope.multi_file_actions import (
    discard_each_resolved_file,
    discard_to_batch_each_resolved_file,
    run_for_each_resolved_file,
)
from ..exceptions import CommandError
from ..i18n import _
from .file_scope import resolve_batch_file_scope, resolve_live_file_scope
from .replacement_input import resolve_replacement_text


def _dispatch_discard_replacement(args: argparse.Namespace) -> None:
    if args.as_text is not None and args.as_stdin:
        raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
    if args.to_batch and args.line_ids and not args.from_batch:
        resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
        resolved_file = resolved_live_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
        replacement_text = resolve_replacement_text(args)
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
                _(
                    "`discard --as` requires `--file`, or `--to` "
                    "with `--line`."
                )
            )
        if args.no_edge_overlap:
            raise CommandError(
                _("`--no-edge-overlap` requires `discard --to --line --as`.")
            )
        if resolved_live_scope.is_multiple:
            raise CommandError(_("Cannot use --as with multiple files."))
        replacement_text = resolve_replacement_text(args)
        command_discard_file_as(
            replacement_text,
            file=resolved_live_scope.optional_file(),
            auto_advance=args.auto_advance,
        )
        return
    raise CommandError(
        _("`discard --as` requires `--file`, or `--to` with `--line`.")
    )


def dispatch_discard_command(args: argparse.Namespace) -> None:
    """Dispatch parsed discard arguments."""
    replacement_requested = args.as_text is not None or args.as_stdin
    if replacement_requested:
        _dispatch_discard_replacement(args)
        return
    if args.no_edge_overlap:
        raise CommandError(
            _("`--no-edge-overlap` requires `discard --to --line --as`.")
        )
    if args.from_batch:
        resolved_batch_scope = resolve_batch_file_scope(
            args.from_batch,
            args.file,
            args.file_patterns,
        )
        run_for_each_resolved_file(
            resolved_batch_scope,
            lambda file: command_discard_from_batch(
                args.from_batch,
                args.line_ids,
                file,
            ),
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
        resolved_file = resolved_live_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
        command_discard_line(
            args.line_ids,
            file=resolved_file,
            auto_advance=args.auto_advance,
        )
    else:
        resolved_live_scope = resolve_live_file_scope(args.file, args.file_patterns)
        if not resolved_live_scope.is_implicit:
            if resolved_live_scope.is_multiple:
                discard_each_resolved_file(
                    list(resolved_live_scope.files),
                    auto_advance=args.auto_advance,
                )
            else:
                command_discard_file(
                    resolved_live_scope.optional_file(),
                    auto_advance=args.auto_advance,
                )
        else:
            command_discard(auto_advance=args.auto_advance)
