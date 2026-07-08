"""Candidate preview builders for batch-source commands."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
import os
from typing import Any

from ..selection import replacement_selection
from ...batch.operation_candidates import (
    OperationCandidatePreview,
    build_apply_candidate_previews,
    build_include_candidate_previews,
)
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.source_selector import BatchSourceSelector
from ...batch.submodule_pointer import is_batch_submodule_pointer
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...core.text_lifecycle import (
    mode_for_text_materialization,
    normalized_text_change_type,
)
from ...data.batch_file_review_selection import (
    translate_batch_file_gutter_ids_to_selection_ids,
)
from ...data.file_review.records import FileReviewAction
from ...data.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git import get_git_repository_root_path


SelectionTranslator = Callable[
    [str, str, set[int], FileReviewAction],
    tuple[set[int], Any],
]


def build_batch_source_candidate_previews(
    *,
    selector: BatchSourceSelector,
    files: dict,
    file_path: str,
    selected_ids: set[int] | None,
    replacement_text: str | ReplacementPayload | None,
    translate_selection_ids: SelectionTranslator = (
        translate_batch_file_gutter_ids_to_selection_ids
    ),
) -> tuple[OperationCandidatePreview, ...]:
    """Return operation candidates for a batch-source candidate selector."""
    operation = selector.candidate_operation
    if operation is None:
        raise ValueError("Candidate preview requires a candidate selector.")

    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(
            _("Candidate preview is only available for text batch entries.")
        )

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(
            _("Batch source content is missing for {file}.").format(file=file_path)
        )

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))

    with batch_source_buffer as batch_source_lines:
        selection_ids_to_apply = selected_ids
        if selected_ids:
            action = (
                FileReviewAction.APPLY_FROM_BATCH
                if operation == "apply"
                else FileReviewAction.INCLUDE_FROM_BATCH
            )
            selection_ids_to_apply, _rendered = translate_selection_ids(
                selector.batch_name,
                file_path,
                selected_ids,
                action,
            )

        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            with ExitStack() as stack:
                source_for_candidates = batch_source_lines
                candidate_ownership = ownership
                replacement_payload = None
                if replacement_text is not None:
                    if operation == "apply":
                        exit_with_error(
                            _("Replacement preview is not valid for apply candidates.")
                        )
                    if not selected_ids:
                        exit_with_error(_("`show --from --as` requires `--line`."))
                    replacement_selection.require_contiguous_display_selection(
                        selected_ids,
                    )
                    replacement_payload = coerce_replacement_payload(replacement_text)
                    try:
                        replacement_view = build_replacement_batch_view_from_lines(
                            batch_source_lines,
                            ownership,
                            replacement_payload,
                        )
                    except ValueError as e:
                        exit_with_error(str(e))
                    replacement_view = stack.enter_context(replacement_view)
                    source_for_candidates = replacement_view.source_buffer
                    candidate_ownership = replacement_view.ownership

                if operation == "apply":
                    worktree_file_mode = mode_for_text_materialization(
                        batch_file_mode,
                        selected_ids,
                        destination_exists=working_exists,
                    )
                    with load_working_tree_file_as_buffer(file_path) as working_lines:
                        return build_apply_candidate_previews(
                            batch_name=selector.batch_name,
                            file_path=file_path,
                            source_lines=source_for_candidates,
                            ownership=candidate_ownership,
                            worktree_lines=working_lines,
                            batch_source_commit=batch_source_commit,
                            file_meta=file_meta,
                            text_change_type=text_change_type,
                            worktree_file_mode=worktree_file_mode,
                            worktree_exists=working_exists,
                            selected_ids=selected_ids,
                            selection_ids=selection_ids_to_apply,
                        )

                index_buffer = load_git_object_as_buffer(f":{file_path}")
                index_exists = index_buffer is not None
                if index_buffer is None:
                    index_buffer = LineBuffer.from_bytes(b"")
                index_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=index_exists,
                )
                worktree_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=working_exists,
                )
                with (
                    index_buffer as index_lines,
                    load_working_tree_file_as_buffer(file_path) as working_lines,
                ):
                    return build_include_candidate_previews(
                        batch_name=selector.batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=worktree_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
                        selected_ids=selected_ids,
                        selection_ids=selection_ids_to_apply,
                        replacement_payload=replacement_payload,
                    )
