"""Apply from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import apply_action as _apply_action
from .batch_source import candidate_execution as _candidate_execution
from ..data.file_review.records import FileReviewAction
from ..i18n import _
from ..utils.git_repository import require_git_repository


def command_apply_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> None:
    """Apply batch changes to working tree using structural merge.

    Args:
        batch_name: Name of batch to apply from
        line_ids: Optional line IDs to apply (requires single-file context)
        file: Optional file path to select from batch.
              If None, applies all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_batch_source_action_context(
        raw_selector,
        operation="apply",
        review_action=FileReviewAction.APPLY_FROM_BATCH,
        command_name="apply",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    selector = context.selector
    batch_name = context.batch_name

    selection = _action_selection.resolve_apply_action_selection(
        context,
        line_ids=line_ids,
        patterns=patterns,
    )
    file = selection.file
    files = selection.files
    selected_ids = selection.selected_ids
    selection_ids_to_apply = selection.selection_ids
    if selector.candidate_ordinal is not None:
        _candidate_execution.execute_apply_candidate(
            batch_name=batch_name,
            raw_selector=raw_selector,
            ordinal=selector.candidate_ordinal,
            files=files,
            selected_ids=selected_ids,
            selection_ids_to_apply=selection_ids_to_apply,
        )
        return

    _apply_action.execute_apply_action(
        batch_name=batch_name,
        context=context,
        selection=selection,
    )

    if line_ids:
        print(_("✓ Applied selected lines from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Applied changes for {file} from batch '{name}' to working tree").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Applied changes from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
