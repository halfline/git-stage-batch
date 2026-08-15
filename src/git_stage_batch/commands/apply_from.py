"""Apply from batch command implementation."""

from __future__ import annotations

from collections.abc import Sequence
import sys
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from .batch_source import action_context as _action_context
from .batch_source import action_selection as _action_selection
from .batch_source import apply_action as _apply_action
from .batch_source import candidate_execution as _candidate_execution
from ..data.file_review.records import FileReviewAction
from ..data.undo.checkpoints import (
    RollbackStatus,
    defer_transaction_completion,
)
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
    *,
    file_paths: Sequence[str] | None = None,
) -> None:
    """Apply batch changes to working tree using structural merge.

    Args:
        batch_name: Name of batch to apply from
        line_ids: Optional line IDs to apply (requires single-file context)
        file: Optional file path to select from batch.
              If None, applies all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        file_paths: Optional pre-resolved literal file paths to apply as one
            transaction. This is mutually exclusive with ``file`` and ``patterns``.
    """
    resolved_file_paths = (
        None if file_paths is None else tuple(dict.fromkeys(file_paths))
    )
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
        resolved_file_count=len(resolved_file_paths or ()),
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
            resolved_file_paths=resolved_file_paths,
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

    file_paths = tuple(files)
    is_candidate = selector.candidate_ordinal is not None
    defer_transaction_completion(
        lambda: _report_apply_success(
            operation_id=operation_id,
            raw_selector=raw_selector,
            batch_name=batch_name,
            file_paths=file_paths,
            selected_lines=bool(line_ids),
            selected_file=file is not None,
            is_candidate=is_candidate,
            journal_state=journal_state,
        ),
        lambda rollback: _report_apply_rollback(
            operation_id=operation_id,
            raw_selector=raw_selector,
            resolved_batch_name=batch_name,
            rollback=rollback,
            journal_state=journal_state,
        ),
    )


def _report_apply_success(
    *,
    operation_id: str,
    raw_selector: str,
    batch_name: str,
    file_paths: tuple[str, ...],
    selected_lines: bool,
    selected_file: bool,
    is_candidate: bool,
    journal_state: _ApplyJournalState,
) -> None:
    """Publish journal and terminal success after the outermost commit."""
    journal_state.update("complete", journal_state.rollback)
    log_journal(
        "apply_from_batch_success",
        operation_id=operation_id,
        batch_name=batch_name,
        batch_selector=raw_selector,
        resolved_batch_name=batch_name,
        files=list(file_paths),
        stage=journal_state.stage,
        rollback=journal_state.rollback,
    )

    if is_candidate:
        return
    if selected_lines:
        message = _(
            "✓ Applied selected lines from batch '{name}' to working tree"
        ).format(name=batch_name)
    elif selected_file:
        message = _(
            "✓ Applied changes for {file} from batch '{name}' to working tree"
        ).format(
            file=display_path(file_paths[0]),
            name=batch_name,
        )
    else:
        message = _("✓ Applied changes from batch '{name}' to working tree").format(
            name=batch_name
        )
    print(message, file=sys.stderr)


def _report_apply_rollback(
    *,
    operation_id: str,
    raw_selector: str,
    resolved_batch_name: str,
    rollback: RollbackStatus,
    journal_state: _ApplyJournalState,
) -> None:
    """Close a locally successful apply journal after enclosing rollback."""
    journal_state.update(journal_state.stage, rollback)
    log_journal(
        "apply_from_batch_failed",
        operation_id=operation_id,
        batch_name=raw_selector,
        batch_selector=raw_selector,
        resolved_batch_name=resolved_batch_name,
        stage=journal_state.stage,
        rollback=journal_state.rollback,
        error_type="EnclosingTransactionRollback",
    )
