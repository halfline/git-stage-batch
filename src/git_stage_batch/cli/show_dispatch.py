"""CLI dispatch for the show command."""

from __future__ import annotations

import argparse

from ..batch.query import read_batch_metadata
from ..batch.source_selector import batch_name_for_source_lookup
from ..batch.validation import batch_exists
from ..commands.show import command_show, command_show_file_list
from ..commands.show_from import command_show_from_batch
from ..exceptions import CommandError
from ..i18n import _
from .file_scope import resolve_batch_file_scope, resolve_live_file_scope
from .replacement_input import resolve_replacement_text


def _validate_show_page_request(
    args: argparse.Namespace,
    *,
    resolved_file_scope,
) -> None:
    lookup_batch = (
        batch_name_for_source_lookup(args.from_batch)
        if args.from_batch
        else None
    )
    if lookup_batch and not batch_exists(lookup_batch):
        raise CommandError(
            _("Batch '{name}' does not exist").format(name=lookup_batch)
        )
    if resolved_file_scope.is_implicit:
        if not (
            args.from_batch
            and lookup_batch is not None
            and batch_exists(lookup_batch)
            and len(read_batch_metadata(lookup_batch).get("files", {})) == 1
        ):
            raise CommandError(
                _(
                    "`show --page` requires `--file` or a single-file "
                    "`--files` match, unless `--from` names a single-file "
                    "batch."
                )
            )
    if resolved_file_scope.is_multiple:
        raise CommandError(_("`show --page` requires exactly one resolved file."))
    if args.line_ids is not None:
        raise CommandError(_("Cannot use `show --page` together with `show --line`."))
    if args.porcelain:
        raise CommandError(_("Cannot use `show --page` with `--porcelain`."))


def _dispatch_show_from_batch(
    args: argparse.Namespace,
    *,
    resolved_file_scope,
    replacement_requested: bool,
) -> None:
    replacement_text = (
        resolve_replacement_text(args)
        if replacement_requested
        else None
    )
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


def _dispatch_show_live(
    args: argparse.Namespace,
    *,
    resolved_file_scope,
) -> None:
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


def dispatch_show_command(args: argparse.Namespace) -> None:
    """Dispatch parsed show arguments."""
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
        _validate_show_page_request(
            args,
            resolved_file_scope=resolved_file_scope,
        )
    if args.from_batch:
        _dispatch_show_from_batch(
            args,
            resolved_file_scope=resolved_file_scope,
            replacement_requested=replacement_requested,
        )
        return
    _dispatch_show_live(args, resolved_file_scope=resolved_file_scope)
