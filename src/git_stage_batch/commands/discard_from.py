"""Discard from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import discard_action as _discard_action
from ..data.file_review.records import FileReviewAction
from ..i18n import _
from ..utils.git_repository import require_git_repository


def command_discard_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Remove batch changes from working tree using structural merge.

    Args:
        batch_name: Name of batch to discard from
        line_ids: Optional line IDs to discard (requires single-file context)
        file: Optional file path to select from batch.
              If None, discards all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_plain_batch_source_action_context(
        raw_selector,
        review_action=FileReviewAction.DISCARD_FROM_BATCH,
        command_name="discard",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
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

    # Success message
    if line_ids:
        print(_("✓ Discarded selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Discarded changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Discarded changes from batch '{name}'").format(name=batch_name), file=sys.stderr)

    print(_("Note: Batch '{name}' still exists (use 'drop' to delete it)").format(name=batch_name), file=sys.stderr)
