"""Command text helpers for operation candidate previews."""

from __future__ import annotations

import shlex

from ..batch.operation_candidate_types import OperationCandidatePreview


def candidate_selector_text(
    batch_name: str,
    operation: str,
    ordinal: int | None = None,
) -> str:
    """Return the selector text for an operation candidate preview."""
    if ordinal is None:
        return f"{batch_name}:{operation}"
    return f"{batch_name}:{operation}:{ordinal}"


def show_candidate_command(
    preview: OperationCandidatePreview,
    ordinal: int | None = None,
) -> str:
    """Return the command that previews one candidate selector."""
    return "git-stage-batch show --from {selector} --file {file}".format(
        selector=candidate_selector_text(
            preview.batch_name,
            preview.operation,
            ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )


def execute_candidate_command(preview: OperationCandidatePreview) -> str:
    """Return the command that executes one candidate selector."""
    return "git-stage-batch {command} --from {selector} --file {file}".format(
        command=preview.operation,
        selector=candidate_selector_text(
            preview.batch_name,
            preview.operation,
            preview.ordinal,
        ),
        file=shlex.quote(preview.file_path),
    )
