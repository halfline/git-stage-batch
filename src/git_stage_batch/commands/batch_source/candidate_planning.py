"""Shared operation-candidate planning for batch-source commands."""

from __future__ import annotations

from pathlib import Path

from . import candidate_inputs as _candidate_inputs
from ...batch.operation_candidate_types import OperationCandidatePreview
from ...batch.operation_candidates import (
    build_apply_candidate_previews as _build_apply_candidate_previews,
)
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...core.buffer import LineBuffer


def plan_apply_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    batch_source_lines: LineBuffer,
    batch_source_commit: str,
    worktree_lines: LineBuffer,
    worktree_target: _candidate_inputs.CandidateWorktreeTarget,
    selected_ids: set[int] | None,
    selection_ids: set[int] | None,
    spool_dir: str | Path | None = None,
) -> tuple[OperationCandidatePreview, ...]:
    """Build apply previews from normalized source and target inputs."""
    ownership_arguments = _spool_arguments(spool_dir)
    with acquire_batch_ownership_for_display_ids_from_lines(
        file_meta,
        batch_source_lines,
        selection_ids,
        **ownership_arguments,
    ) as ownership:
        return _build_apply_candidate_previews(
            batch_name=batch_name,
            file_path=file_path,
            source_lines=batch_source_lines,
            ownership=ownership,
            worktree_lines=worktree_lines,
            batch_source_commit=batch_source_commit,
            file_meta=file_meta,
            text_change_type=worktree_target.text_change_type,
            worktree_file_mode=worktree_target.file_mode,
            worktree_exists=worktree_target.exists,
            selected_ids=selected_ids,
            selection_ids=selection_ids,
            **ownership_arguments,
        )


def _spool_arguments(spool_dir: str | Path | None) -> dict[str, str | Path]:
    if spool_dir is None:
        return {}
    return {"spool_dir": spool_dir}
