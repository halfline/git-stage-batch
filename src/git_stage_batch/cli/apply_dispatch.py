"""CLI dispatch for the apply command."""

from __future__ import annotations

import argparse

from ..commands.apply_from import command_apply_from_batch
from ..commands.file_scope.multi_file_actions import run_for_each_resolved_file
from ..data.file_review.records import FileReviewAction
from .file_scope import resolve_batch_file_scope


def dispatch_apply_command(args: argparse.Namespace) -> None:
    """Dispatch parsed apply arguments."""
    line_ids = args.line_ids if hasattr(args, "line_ids") else None
    resolved_file_scope = resolve_batch_file_scope(
        args.from_batch,
        args.file,
        args.file_patterns,
        selected_action=FileReviewAction.APPLY_FROM_BATCH,
        command_name="apply",
        line_ids=line_ids,
    )
    if resolved_file_scope.is_multiple and line_ids is None:
        command_apply_from_batch(
            args.from_batch,
            line_ids=line_ids,
            file_paths=resolved_file_scope.files,
        )
        return
    run_for_each_resolved_file(
        resolved_file_scope,
        lambda file: command_apply_from_batch(
            args.from_batch,
            line_ids=line_ids,
            file=file,
        ),
        line_ids=line_ids,
    )
