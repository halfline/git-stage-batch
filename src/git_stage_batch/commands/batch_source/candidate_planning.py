"""Shared operation-candidate planning for batch-source commands."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Callable

from . import candidate_inputs as _candidate_inputs
from ...batch.operation_candidate_types import OperationCandidatePreview
from ...batch.operation_candidates import (
    build_apply_candidate_previews as _build_apply_candidate_previews,
    build_include_candidate_previews as _build_include_candidate_previews,
)
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.ownership.metadata_types import BatchOwnershipMetadata
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload


class CandidateReplacementError(ValueError):
    """Raised when replacement input cannot form a candidate source view."""


def plan_apply_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    file_meta: BatchFileMetadataDict,
    batch_source_lines: LineBuffer,
    batch_source_commit: str,
    worktree_lines: LineBuffer,
    worktree_target: _candidate_inputs.CandidateWorktreeTarget,
    selected_ids: set[int] | None,
    selection_ids: set[int] | None,
    spool_dir: str | Path | None = None,
    capture_selected_ownership: (
        Callable[[BatchOwnershipMetadata], None] | None
    ) = None,
) -> tuple[OperationCandidatePreview, ...]:
    """Build apply previews from normalized source and target inputs."""
    with acquire_batch_ownership_for_display_ids_from_lines(
        file_meta,
        batch_source_lines,
        selection_ids,
        spool_dir=spool_dir,
    ) as ownership:
        if capture_selected_ownership is not None:
            capture_selected_ownership(
                ownership.to_attribution_metadata_dict()
            )
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
            spool_dir=spool_dir,
        )


def plan_include_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    file_meta: BatchFileMetadataDict,
    batch_source_lines: LineBuffer,
    batch_source_commit: str,
    index_lines: LineBuffer,
    index_target: _candidate_inputs.CandidateIndexTarget,
    worktree_lines: LineBuffer,
    worktree_target: _candidate_inputs.CandidateWorktreeTarget,
    selected_ids: set[int] | None,
    selection_ids: set[int] | None,
    replacement_payload: ReplacementPayload | None,
    spool_dir: str | Path | None = None,
) -> tuple[OperationCandidatePreview, ...]:
    """Build include previews from normalized source and target inputs."""
    with ExitStack() as stack:
        ownership = stack.enter_context(
            acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids,
                spool_dir=spool_dir,
            )
        )
        source_for_candidates = batch_source_lines
        candidate_ownership = ownership
        if replacement_payload is not None:
            try:
                replacement_view = build_replacement_batch_view_from_lines(
                    batch_source_lines,
                    ownership,
                    replacement_payload,
                    spool_dir=spool_dir,
                )
            except ValueError as error:
                raise CandidateReplacementError(str(error)) from error
            replacement_view = stack.enter_context(replacement_view)
            source_for_candidates = replacement_view.source_buffer
            candidate_ownership = replacement_view.ownership

        return _build_include_candidate_previews(
            batch_name=batch_name,
            file_path=file_path,
            source_lines=source_for_candidates,
            ownership=candidate_ownership,
            index_lines=index_lines,
            worktree_lines=worktree_lines,
            batch_source_commit=batch_source_commit,
            file_meta=file_meta,
            text_change_type=worktree_target.text_change_type,
            index_file_mode=index_target.file_mode,
            worktree_file_mode=worktree_target.file_mode,
            index_exists=index_target.exists,
            worktree_exists=worktree_target.exists,
            selected_ids=selected_ids,
            selection_ids=selection_ids,
            replacement_payload=replacement_payload,
            spool_dir=spool_dir,
        )
