"""Candidate preview counting for batch-source action commands."""

from __future__ import annotations

from contextlib import ExitStack
import os

from . import candidate_previews as _candidate_previews
from ...batch.operation_candidates import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
    build_apply_candidate_previews,
    build_include_candidate_previews,
)
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.submodule_pointer import is_batch_submodule_pointer
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload
from ...core.text_lifecycle import (
    mode_for_text_materialization,
    normalized_text_change_type,
)
from ...utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ...exceptions import MergeError
from ...utils.git import get_git_repository_root_path


def count_apply_candidate_previews_for_file(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    selection_ids_to_apply: set[int] | None,
) -> CandidatePreviewCount:
    """Return previewable apply candidate counts for one text batch file."""
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        return CandidatePreviewCount()
    batch_source_commit = file_meta.get("batch_source_commit")
    if not batch_source_commit:
        return CandidatePreviewCount()
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        return CandidatePreviewCount()

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selection_ids_to_apply,
        destination_exists=working_exists,
    )
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

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
                    batch_source_commit=batch_source_commit,
                    file_meta=file_meta,
                    text_change_type=text_change_type,
                    worktree_file_mode=file_mode,
                    worktree_exists=working_exists,
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
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        return CandidatePreviewCount()
    batch_source_commit = file_meta.get("batch_source_commit")
    if not batch_source_commit:
        return CandidatePreviewCount()
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        return CandidatePreviewCount()

    index_buffer = load_git_object_as_buffer(f":{file_path}")
    index_exists = index_buffer is not None
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"")

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))
    index_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selection_ids_to_include,
        destination_exists=index_exists,
    )
    working_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selection_ids_to_include,
        destination_exists=working_exists,
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
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=working_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
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
