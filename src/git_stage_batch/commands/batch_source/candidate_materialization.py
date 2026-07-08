"""Reviewed candidate materialization for batch-source action commands."""

from __future__ import annotations

from dataclasses import dataclass
import os

from . import candidate_previews as _candidate_previews
from ...batch.operation_candidates import (
    OperationCandidatePreview,
    TargetCandidatePreview,
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
from ...exceptions import MergeError, exit_with_error
from ...i18n import _
from ...utils.git import get_git_repository_root_path


@dataclass(frozen=True)
class ApplyCandidateMaterialization:
    """Materialized apply candidate plus metadata needed by command execution."""

    preview: OperationCandidatePreview
    previews: tuple[OperationCandidatePreview, ...]
    file_path: str
    file_mode: str | None

    @property
    def target(self) -> TargetCandidatePreview:
        return self.preview.targets[0]

    def close(self) -> None:
        _candidate_previews.close_candidate_previews(self.previews)


def materialize_apply_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict,
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
) -> ApplyCandidateMaterialization:
    """Return the reviewed apply candidate selected by the user."""
    if len(files) != 1:
        exit_with_error(_("Candidate execution requires exactly one file."))
    file_path, file_meta = next(iter(files.items()))
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(
            _("Candidate execution is only available for text batch entries.")
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
    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selected_ids,
        destination_exists=working_exists,
    )
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

    with (
        batch_source_buffer as batch_source_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            try:
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
                    selected_ids=selected_ids,
                    selection_ids=selection_ids_to_apply,
                )
            except MergeError as e:
                exit_with_error(str(e))

            try:
                preview = _candidate_previews.require_candidate_preview_for_ordinal(
                    previews,
                    ordinal,
                    batch_name=batch_name,
                    operation="apply",
                    file_path=file_path,
                )
                _candidate_previews.require_candidate_preview_state(
                    preview,
                    ordinal,
                    selector=raw_selector,
                    file_path=file_path,
                )
            except Exception:
                _candidate_previews.close_candidate_previews(previews)
                raise

    return ApplyCandidateMaterialization(
        preview=preview,
        previews=previews,
        file_path=file_path,
        file_mode=file_mode,
    )
