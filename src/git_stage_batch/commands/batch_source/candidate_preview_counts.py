"""Candidate preview counting for batch-source action commands."""

from __future__ import annotations

from contextlib import ExitStack

from . import candidate_inputs as _candidate_inputs
from . import candidate_previews as _candidate_previews
from ...batch.operation_candidate_types import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
)
from ...batch.operation_candidates import (
    build_apply_candidate_previews,
    build_include_candidate_previews,
)
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload
from ...utils.repository_buffers import (
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
) -> CandidatePreviewCount:
    """Return previewable apply candidate counts for one text batch file."""
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        return CandidatePreviewCount()
    batch_source_ref = _candidate_inputs.candidate_batch_source_ref(file_path, file_meta)
    if batch_source_ref is None:
        return CandidatePreviewCount()
    batch_source_buffer = read_git_object_buffer_or_none(batch_source_ref.object_spec)
    if batch_source_buffer is None:
        return CandidatePreviewCount()

    worktree_target = _candidate_inputs.candidate_worktree_text_target(
        file_path=file_path,
        file_meta=file_meta,
        selected_ids=selection_ids_to_apply,
    )

    try:
        with (
            batch_source_buffer as batch_source_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_apply,
            ) as ownership:
                previews = build_apply_candidate_previews(
                    batch_name=batch_name,
                    file_path=file_path,
                    source_lines=batch_source_lines,
                    ownership=ownership,
                    worktree_lines=working_lines,
                    batch_source_commit=batch_source_ref.commit,
                    file_meta=file_meta,
                    text_change_type=worktree_target.text_change_type,
                    worktree_file_mode=worktree_target.file_mode,
                    worktree_exists=worktree_target.exists,
                    selected_ids=selection_ids_to_apply,
                    selection_ids=selection_ids_to_apply,
                )
                count = len(previews)
                _candidate_previews.close_candidate_previews(previews)
                return CandidatePreviewCount(count=count)
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
) -> CandidatePreviewCount:
    """Return previewable include candidate counts for one text batch file."""
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        return CandidatePreviewCount()
    batch_source_ref = _candidate_inputs.candidate_batch_source_ref(file_path, file_meta)
    if batch_source_ref is None:
        return CandidatePreviewCount()
    batch_source_buffer = read_git_object_buffer_or_none(batch_source_ref.object_spec)
    if batch_source_buffer is None:
        return CandidatePreviewCount()

    index_buffer = read_git_object_buffer_or_none(f":{file_path}")
    index_exists = index_buffer is not None
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"")

    index_target = _candidate_inputs.candidate_index_text_target(
        file_meta=file_meta,
        selected_ids=selection_ids_to_include,
        index_exists=index_exists,
    )
    worktree_target = _candidate_inputs.candidate_worktree_text_target(
        file_path=file_path,
        file_meta=file_meta,
        selected_ids=selection_ids_to_include,
    )

    try:
        with (
            batch_source_buffer as batch_source_lines,
            index_buffer as index_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_include,
            ) as ownership:
                with ExitStack() as stack:
                    source_for_candidates = batch_source_lines
                    candidate_ownership = ownership
                    if replacement_payload is not None:
                        replacement_view = build_replacement_batch_view_from_lines(
                            batch_source_lines,
                            ownership,
                            replacement_payload,
                        )
                        replacement_view = stack.enter_context(replacement_view)
                        source_for_candidates = replacement_view.source_buffer
                        candidate_ownership = replacement_view.ownership
                    previews = build_include_candidate_previews(
                        batch_name=batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_ref.commit,
                        file_meta=file_meta,
                        text_change_type=worktree_target.text_change_type,
                        index_file_mode=index_target.file_mode,
                        worktree_file_mode=worktree_target.file_mode,
                        index_exists=index_target.exists,
                        worktree_exists=worktree_target.exists,
                        selected_ids=selection_ids_to_include,
                        selection_ids=selection_ids_to_include,
                        replacement_payload=replacement_payload,
                    )
                count = len(previews)
                _candidate_previews.close_candidate_previews(previews)
                return CandidatePreviewCount(count=count)
    except CandidateEnumerationLimitError as e:
        return CandidatePreviewCount(too_many=True, error=str(e))
    except MergeError:
        return CandidatePreviewCount()
    except Exception as e:
        return CandidatePreviewCount(error=str(e))
