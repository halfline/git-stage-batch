"""Reviewed candidate materialization for batch-source action commands."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

from . import candidate_inputs as _candidate_inputs
from . import candidate_previews as _candidate_previews
from ...batch.operation_candidates import (
    OperationCandidatePreview,
    TargetCandidatePreview,
    build_apply_candidate_previews,
    build_include_candidate_previews,
)
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload
from ...data.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ...exceptions import MergeError, exit_with_error
from ...i18n import _


@dataclass(frozen=True)
class ApplyCandidateMaterialization:
    """Materialized apply candidate plus metadata needed by command execution."""

    preview: OperationCandidatePreview
    previews: tuple[OperationCandidatePreview, ...]
    file_path: str
    file_mode: str | None

    @property
    def target(self) -> TargetCandidatePreview:
        return self.preview.require_target("worktree")

    def close(self) -> None:
        _candidate_previews.close_candidate_previews(self.previews)


@dataclass(frozen=True)
class IncludeCandidateMaterialization:
    """Materialized include candidate plus metadata needed by command execution."""

    preview: OperationCandidatePreview
    previews: tuple[OperationCandidatePreview, ...]
    file_path: str
    index_file_mode: str | None
    worktree_file_mode: str | None

    @property
    def index_target(self) -> TargetCandidatePreview:
        return self.preview.require_target("index")

    @property
    def worktree_target(self) -> TargetCandidatePreview:
        return self.preview.require_target("worktree")

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
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        exit_with_error(
            _("Candidate execution is only available for text batch entries.")
        )

    batch_source_ref = _candidate_inputs.require_candidate_batch_source_ref(
        file_path,
        file_meta,
    )
    batch_source_buffer = load_git_object_as_buffer(batch_source_ref.object_spec)
    if batch_source_buffer is None:
        exit_with_error(
            _("Batch source content is missing for {file}.").format(file=file_path)
        )

    worktree_target = _candidate_inputs.candidate_worktree_text_target(
        file_path=file_path,
        file_meta=file_meta,
        selected_ids=selected_ids,
    )

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
                    batch_source_commit=batch_source_ref.commit,
                    file_meta=file_meta,
                    text_change_type=worktree_target.text_change_type,
                    worktree_file_mode=worktree_target.file_mode,
                    worktree_exists=worktree_target.exists,
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
        file_mode=worktree_target.file_mode,
    )


def materialize_include_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict,
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
) -> IncludeCandidateMaterialization:
    """Return the reviewed include candidate selected by the user."""
    if len(files) != 1:
        exit_with_error(_("Candidate execution requires exactly one file."))
    file_path, file_meta = next(iter(files.items()))
    if not _candidate_inputs.is_text_candidate_entry(file_meta):
        exit_with_error(
            _("Candidate execution is only available for text batch entries.")
        )

    batch_source_ref = _candidate_inputs.require_candidate_batch_source_ref(
        file_path,
        file_meta,
    )
    batch_source_buffer = load_git_object_as_buffer(batch_source_ref.object_spec)
    if batch_source_buffer is None:
        exit_with_error(
            _("Batch source content is missing for {file}.").format(file=file_path)
        )

    index_buffer = load_git_object_as_buffer(f":{file_path}")
    index_exists = index_buffer is not None
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"")
    index_target = _candidate_inputs.candidate_index_text_target(
        file_meta=file_meta,
        selected_ids=selected_ids,
        index_exists=index_exists,
    )
    worktree_target = _candidate_inputs.candidate_worktree_text_target(
        file_path=file_path,
        file_meta=file_meta,
        selected_ids=selected_ids,
    )

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
                try:
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
                        selected_ids=selected_ids,
                        selection_ids=selection_ids_to_include,
                        replacement_payload=replacement_payload,
                    )
                except (MergeError, ValueError) as e:
                    exit_with_error(str(e))

            try:
                preview = _candidate_previews.require_candidate_preview_for_ordinal(
                    previews,
                    ordinal,
                    batch_name=batch_name,
                    operation="include",
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

    return IncludeCandidateMaterialization(
        preview=preview,
        previews=previews,
        file_path=file_path,
        index_file_mode=index_target.file_mode,
        worktree_file_mode=worktree_target.file_mode,
    )
