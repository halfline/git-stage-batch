"""Include from batch command implementation."""

from __future__ import annotations

import sys
from typing import Optional

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import candidate_execution as _candidate_execution
from .batch_source import include_action as _include_action
from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..data.file_review.records import FileReviewAction
from ..i18n import _
from ..utils.git_index import git_refresh_index
from ..utils.git_repository import require_git_repository


def command_include_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    replacement_text: Optional[str | ReplacementPayload] = None,
) -> None:
    """Stage batch changes to index and working tree using structural merge.

    Args:
        batch_name: Name of batch to include from
        line_ids: Optional line IDs to include (requires single-file context)
        file: Optional file path to select from batch.
              If None, includes all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        replacement_text: Optional replacement text for selected batch lines.
    """
    require_git_repository()
    raw_selector = batch_name
    context = _action_context.resolve_batch_source_action_context(
        raw_selector,
        operation="include",
        review_action=FileReviewAction.INCLUDE_FROM_BATCH,
        command_name="include",
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    selector = context.selector
    batch_name = context.batch_name

    # Refresh index to ensure git's cached stat info is up-to-date
    git_refresh_index(check=False)

    replacement_payload = (
        coerce_replacement_payload(replacement_text)
        if replacement_text is not None
        else None
    )
    selection = _action_selection.resolve_include_action_selection(
        context,
        line_ids=line_ids,
        patterns=patterns,
        replacement_payload=replacement_payload,
    )
    file = selection.file
    files = selection.files
    selected_ids = selection.selected_ids
    selection_ids_to_include = selection.selection_ids
    if selector.candidate_ordinal is not None:
        _candidate_execution.execute_include_candidate(
            batch_name=batch_name,
            raw_selector=raw_selector,
            ordinal=selector.candidate_ordinal,
            files=files,
            selected_ids=selected_ids,
            selection_ids_to_include=selection_ids_to_include,
            replacement_payload=replacement_payload,
        )
        return

    _include_action.execute_include_action(
        batch_name=batch_name,
        context=context,
        selection=selection,
        replacement_payload=replacement_payload,
    )

    if replacement_payload is not None and line_ids:
        print(
            _("✓ Staged selected lines as replacement from batch '{name}'").format(name=batch_name),
            file=sys.stderr,
        )
    elif line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
