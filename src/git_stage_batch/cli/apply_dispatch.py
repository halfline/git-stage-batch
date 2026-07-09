"""CLI dispatch for the apply command."""

from __future__ import annotations

import argparse
import shlex

from ..commands.apply_from import command_apply_from_batch
from ..commands.file_scope.multi_file_actions import run_for_each_resolved_file
from .file_scope import resolve_batch_file_scope


def dispatch_apply_command(args: argparse.Namespace) -> None:
    """Dispatch parsed apply arguments."""
    resolved_file_scope = resolve_batch_file_scope(
        args.from_batch,
        args.file,
        args.file_patterns,
    )
    line_ids = args.line_ids if hasattr(args, "line_ids") else None
    run_for_each_resolved_file(
        resolved_file_scope,
        lambda file: command_apply_from_batch(
            args.from_batch,
            line_ids=line_ids,
            file=file,
        ),
        line_ids=line_ids,
        undo_operation=f"apply --from {shlex.quote(args.from_batch)}",
        worktree_paths=resolved_file_scope.files,
    )
