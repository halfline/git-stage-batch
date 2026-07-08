"""Candidate preview counting for batch-source action commands."""

from __future__ import annotations

import os

from . import candidate_previews as _candidate_previews
from ...batch.operation_candidates import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
    build_apply_candidate_previews,
)
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.submodule_pointer import is_batch_submodule_pointer
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
