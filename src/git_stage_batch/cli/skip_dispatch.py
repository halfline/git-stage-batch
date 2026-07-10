"""CLI dispatch for the skip command."""

from __future__ import annotations

import argparse

from ..commands.file_scope.multi_file_actions import skip_each_resolved_file
from ..commands.skip import command_skip, command_skip_file, command_skip_line
from ..i18n import _
from .file_scope import resolve_live_file_scope


def dispatch_skip_command(args: argparse.Namespace) -> None:
    """Dispatch parsed skip arguments."""
    resolved_file_scope = resolve_live_file_scope(args.file, args.file_patterns)
    if args.line_ids:
        resolved_file = resolved_file_scope.require_single_file(
            _("Cannot use --lines with multiple files.")
        )
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
