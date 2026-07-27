"""Candidate preview counting for batch-source action commands."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

from . import candidate_inputs as _candidate_inputs
from . import candidate_planning as _candidate_planning
from . import candidate_previews as _candidate_previews
from ...batch.operation_candidate_types import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
)
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload
from ...data.file_target_identity import IndexIdentity
from ...utils.repository_buffers import (
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...exceptions import MergeError


def count_apply_candidate_previews_for_file(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    selection_ids_to_apply: set[int] | None,
    batch_source_object_id: str | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    spool_dir: str | Path | None = None,
) -> CandidatePreviewCount:
    """Return previewable apply candidate counts for one text batch file."""
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        return CandidatePreviewCount()
    batch_source_ref = _candidate_inputs.candidate_batch_source_ref(file_path, file_meta)
    if batch_source_ref is None:
        return CandidatePreviewCount()
    try:
        if batch_source_object_id is None:
            batch_source_spec = batch_source_ref.object_spec
            batch_source_buffer = (
                read_git_object_buffer_or_none(batch_source_spec)
                if spool_dir is None
                else read_git_object_buffer_or_none(
                    batch_source_spec,
                    spool_dir=spool_dir,
                )
            )
        else:
            batch_source_buffer = load_git_blob_as_buffer(
                batch_source_object_id,
                spool_dir=spool_dir,
            )
        if batch_source_buffer is None:
            return CandidatePreviewCount()

        with ExitStack() as stack:
            batch_source_lines = stack.enter_context(batch_source_buffer)
            worktree_target = _candidate_inputs.candidate_worktree_text_target(
                file_path=file_path,
                file_meta=file_meta,
                selected_ids=selection_ids_to_apply,
                captured_working_tree_exists=(
                    captured_working_tree_exists
                ),
            )
            if working_tree_artifact_path is None:
                working_tree_buffer = (
                    load_working_tree_file_as_buffer(file_path)
                    if spool_dir is None
                    else load_working_tree_file_as_buffer(
                        file_path,
                        spool_dir=spool_dir,
                    )
                )
            else:
                working_tree_buffer = LineBuffer.from_path(
                    working_tree_artifact_path,
                    spool_dir=spool_dir,
                )
            working_lines = stack.enter_context(working_tree_buffer)
            previews = _candidate_planning.plan_apply_candidate_previews(
                batch_name=batch_name,
                file_path=file_path,
                file_meta=file_meta,
                batch_source_lines=batch_source_lines,
                batch_source_commit=batch_source_ref.commit,
                worktree_lines=working_lines,
                worktree_target=worktree_target,
                selected_ids=selection_ids_to_apply,
                selection_ids=selection_ids_to_apply,
                spool_dir=spool_dir,
            )
            try:
                return CandidatePreviewCount(count=len(previews))
            finally:
                _candidate_previews.close_candidate_previews(previews)
    except CandidateEnumerationLimitError as e:
        return CandidatePreviewCount(too_many=True, error=str(e))
    except MergeError:
        return CandidatePreviewCount()
    except Exception as e:
        return CandidatePreviewCount(error=str(e))


def count_include_candidate_previews_for_file(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
    batch_source_object_id: str | None = None,
    captured_index_identity: IndexIdentity | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    spool_dir: str | Path | None = None,
) -> CandidatePreviewCount:
    """Return previewable include candidate counts for one text batch file."""
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        return CandidatePreviewCount()
    batch_source_ref = _candidate_inputs.candidate_batch_source_ref(file_path, file_meta)
    if batch_source_ref is None:
        return CandidatePreviewCount()
    try:
        if batch_source_object_id is None:
            batch_source_buffer = read_git_object_buffer_or_none(
                batch_source_ref.object_spec
            )
        else:
            batch_source_buffer = load_git_blob_as_buffer(
                batch_source_object_id,
                spool_dir=spool_dir,
            )
        if batch_source_buffer is None:
            return CandidatePreviewCount()

        with ExitStack() as stack:
            batch_source_lines = stack.enter_context(batch_source_buffer)
            if captured_index_identity is None:
                index_buffer = read_git_object_buffer_or_none(f":{file_path}")
                index_exists = index_buffer is not None
            else:
                index_exists = captured_index_identity.exists
                index_object_id = captured_index_identity.content_object_id
                index_buffer = (
                    load_git_blob_as_buffer(
                        index_object_id,
                        spool_dir=spool_dir,
                    )
                    if index_object_id is not None
                    else None
                )
            if index_buffer is None:
                index_buffer = LineBuffer.from_bytes(
                    b"",
                    spool_dir=spool_dir,
                )
            index_lines = stack.enter_context(index_buffer)
            index_target = _candidate_inputs.candidate_index_text_target(
                file_meta=file_meta,
                selected_ids=selection_ids_to_include,
                index_exists=index_exists,
            )
            worktree_target = (
                _candidate_inputs.candidate_worktree_text_target(
                    file_path=file_path,
                    file_meta=file_meta,
                    selected_ids=selection_ids_to_include,
                    captured_working_tree_exists=(
                        captured_working_tree_exists
                    ),
                )
            )
            if working_tree_artifact_path is None:
                working_buffer = (
                    load_working_tree_file_as_buffer(file_path)
                    if spool_dir is None
                    else load_working_tree_file_as_buffer(
                        file_path,
                        spool_dir=spool_dir,
                    )
                )
            else:
                working_buffer = LineBuffer.from_path(
                    working_tree_artifact_path,
                    spool_dir=spool_dir,
                )
            working_lines = stack.enter_context(working_buffer)
            previews = _candidate_planning.plan_include_candidate_previews(
                batch_name=batch_name,
                file_path=file_path,
                file_meta=file_meta,
                batch_source_lines=batch_source_lines,
                batch_source_commit=batch_source_ref.commit,
                index_lines=index_lines,
                index_target=index_target,
                worktree_lines=working_lines,
                worktree_target=worktree_target,
                selected_ids=selection_ids_to_include,
                selection_ids=selection_ids_to_include,
                replacement_payload=replacement_payload,
                spool_dir=spool_dir,
            )
            try:
                return CandidatePreviewCount(count=len(previews))
            finally:
                _candidate_previews.close_candidate_previews(previews)
    except CandidateEnumerationLimitError as e:
        return CandidatePreviewCount(too_many=True, error=str(e))
    except MergeError:
        return CandidatePreviewCount()
    except Exception as e:
        return CandidatePreviewCount(error=str(e))
