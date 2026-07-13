"""Worktree refusal helpers for batch-source commands."""

from __future__ import annotations

from collections.abc import Iterable

from ...exceptions import exit_with_error
from ...i18n import _


def refuse_incompatible_worktree_action(
    *,
    batch_name: str,
    file_paths: Iterable[str],
    error: Exception | None = None,
) -> None:
    """Exit when batch-source execution is incompatible with the worktree."""
    paths = tuple(file_paths)
    error_suffix = (
        _(" Underlying error: {error}").format(error=error)
        if error is not None
        else ""
    )
    if len(paths) == 1:
        file_path = paths[0]
        exit_with_error(
            _(
                "Batch '{batch}' contains changes to {file} that are "
                "incompatible with the current working tree. "
                "Use 'git-stage-batch show --from {batch}' to review the batch."
                "{error_suffix}"
            ).format(
                batch=batch_name,
                file=file_path,
                error_suffix=error_suffix,
            )
        )
    exit_with_error(
        _(
            "Batch '{batch}' contains changes to one or more files that are "
            "incompatible with the current working tree. "
            "Use 'git-stage-batch show --from {batch}' to review the batch."
            "{error_suffix}"
        ).format(batch=batch_name, error_suffix=error_suffix)
    )
