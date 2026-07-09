"""Review-state completion for batch-source action commands."""

from __future__ import annotations

from collections.abc import Iterable

from ...data.file_review.state import finish_review_scoped_line_action
from .action_context import BatchSourceActionContext


def finish_batch_source_action_review(
    context: BatchSourceActionContext,
    file_paths: Iterable[str],
) -> None:
    """Mark reviewed batch-source files as handled after an action."""
    review_state = context.scope_resolution.review_state
    for file_path in file_paths:
        finish_review_scoped_line_action(review_state, file_path=file_path)
