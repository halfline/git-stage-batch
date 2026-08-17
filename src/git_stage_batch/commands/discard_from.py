"""Discard from batch command implementation."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import discard_action as _discard_action
from ..data.file_review.records import FileReviewAction
from ..data.undo.checkpoints import defer_transaction_success
from ..git_paths import display_path
from ..i18n import _
from ..utils.git_repository import require_git_repository


def command_discard_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    *,
    file_paths: Sequence[str] | None = None,
) -> None:
    """Remove batch changes from working tree using structural merge.

    Args:
        batch_name: Name of batch to discard from
        line_ids: Optional line IDs to discard (requires single-file context)
        file: Optional file path to select from batch.
              If None, discards all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        file_paths: Optional pre-resolved literal file paths to discard as one
            transaction. This is mutually exclusive with ``file`` and ``patterns``.
    """
    resolved_file_paths = (
        None if file_paths is None else tuple(dict.fromkeys(file_paths))
    )
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_plain_batch_source_action_context(
        raw_selector,
        review_action=FileReviewAction.DISCARD_FROM_BATCH,
        command_name="discard",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
        resolved_file_paths=resolved_file_paths,
    )
    batch_name = context.batch_name

    selection = _action_selection.resolve_discard_action_selection(
        context,
        line_ids=line_ids,
        patterns=patterns,
    )
    file = selection.file
    files = selection.files
    _discard_action.execute_discard_action(
        batch_name=batch_name,
        selection=selection,
    )

    file_paths = tuple(files)
    defer_transaction_success(
        lambda: _print_discard_success(
            batch_name=batch_name,
            file_paths=file_paths,
            selected_lines=bool(line_ids),
            selected_file=file is not None,
        )
    )


def _print_discard_success(
    *,
    batch_name: str,
    file_paths: tuple[str, ...],
    selected_lines: bool,
    selected_file: bool,
) -> None:
    """Print discard success only after the outermost commit."""
    if selected_lines:
        message = _("✓ Discarded selected lines from batch '{name}'").format(
            name=batch_name
        )
    elif selected_file:
        message = _("✓ Discarded changes for {file} from batch '{name}'").format(
            file=display_path(file_paths[0]),
            name=batch_name,
        )
    else:
        message = _("✓ Discarded changes from batch '{name}'").format(name=batch_name)
    print(message, file=sys.stderr)
    print(
        _("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(
            name=batch_name
        ),
        file=sys.stderr,
    )
