"""Apply from batch command implementation."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import apply_action as _apply_action
from .batch_source import candidate_execution as _candidate_execution
from ..data.file_review.records import FileReviewAction
from ..git_paths import display_path
from ..i18n import _
from ..utils.git_repository import require_git_repository
from ..utils.journal import log_journal


@dataclass(slots=True)
class _ApplyJournalState:
    """Last observable apply phase for a terminal journal event."""

    stage: str = "repository"
    rollback: str = "not-started"

    def update(self, stage: str, rollback: str) -> None:
        self.stage = stage
        self.rollback = rollback


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
    raw_selector = batch_name
    resolved_batch_name: str | None = None
    operation_id = uuid4().hex
    journal_state = _ApplyJournalState()
    log_journal(
        "apply_from_batch_start",
        operation_id=operation_id,
        batch_name=raw_selector,
        batch_selector=raw_selector,
        line_ids=line_ids,
        requested_file_path=file,
        pattern_count=len(patterns or ()),
    )
    try:
        require_git_repository()
        journal_state.update("context", "not-started")
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
        resolved_batch_name = batch_name

        journal_state.update("selection", "not-started")
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
            revision = context.metadata.get("revision")
            if not isinstance(revision, str) or not revision:
                raise ValueError("validated batch metadata omitted its revision")
            _candidate_execution.execute_apply_candidate(
                batch_name=batch_name,
                batch_revision=revision,
                raw_selector=raw_selector,
                ordinal=selector.candidate_ordinal,
                files=files,
                selected_ids=selected_ids,
                selection_ids_to_apply=selection_ids_to_apply,
                journal_progress=journal_state.update,
            )
        else:
            _apply_action.execute_apply_action(
                batch_name=batch_name,
                context=context,
                selection=selection,
                journal_progress=journal_state.update,
            )
    except BaseException as error:
        log_journal(
            "apply_from_batch_failed",
            operation_id=operation_id,
            batch_name=raw_selector,
            batch_selector=raw_selector,
            resolved_batch_name=resolved_batch_name,
            stage=journal_state.stage,
            rollback=journal_state.rollback,
            error_type=type(error).__name__,
        )
        raise

    journal_state.update("complete", journal_state.rollback)
    log_journal(
        "apply_from_batch_success",
        operation_id=operation_id,
        batch_name=batch_name,
        batch_selector=raw_selector,
        resolved_batch_name=batch_name,
        files=list(files),
        stage=journal_state.stage,
        rollback=journal_state.rollback,
    )

    if selector.candidate_ordinal is not None:
        return
    if line_ids:
        print(_("✓ Applied selected lines from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(
            _(
                "✓ Applied changes for {file} from batch '{name}' to working tree"
            ).format(
                file=display_path(next(iter(files))),
                name=batch_name,
            ),
            file=sys.stderr,
        )
    else:
        print(_("✓ Applied changes from batch '{name}' to working tree").format(name=batch_name), file=sys.stderr)
