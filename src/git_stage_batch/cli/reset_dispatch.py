"""CLI dispatch for the reset command."""

from __future__ import annotations

import argparse
import shlex

from ..commands.file_scope.multi_file_actions import run_for_each_resolved_file
from ..commands.reset import command_reset_from_batch
from .file_scope import resolve_batch_file_scope


def dispatch_reset_command(args: argparse.Namespace) -> None:
    """Dispatch parsed reset arguments."""
    resolved_file_scope = resolve_batch_file_scope(
        args.from_batch,
        args.file,
        args.file_patterns,
    )
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
