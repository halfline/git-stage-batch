"""Operation-level candidate planning and state for batch apply/include."""

from __future__ import annotations

from itertools import product

from ..core.buffer import LineBuffer
from ..core.replacement import ReplacementPayload
from ..core.text_lifecycle import selected_text_target_change_type
from ..exceptions import AtomicUnitError, MergeError
from .merge import (
    enumerate_merge_batch_candidates_from_line_sequences,
    merge_batch_from_line_sequences_as_buffer,
)
from .merge_candidates import (
    MergeCandidate,
)
from .operation_candidate_types import (
    CandidateEnumerationLimitError as _CandidateEnumerationLimitError,
    CandidateOperation as _CandidateOperation,
    CandidateTarget as _CandidateTarget,
    MAX_OPERATION_CANDIDATES as _MAX_OPERATION_CANDIDATES,
    OperationCandidatePreview as _OperationCandidatePreview,
    TargetCandidatePreview as _TargetCandidatePreview,
)
from .operation_candidate_fingerprints import (
    batch_fingerprint as _fingerprint_batch,
    candidate_id as _fingerprint_candidate_id,
    scope_fingerprint as _fingerprint_scope,
    target_fingerprint as _fingerprint_target,
    target_result_fingerprint as _fingerprint_target_result,
)


def _merge_candidates_or_unambiguous(
    source_lines: LineBuffer,
    ownership,
    target_lines: LineBuffer,
) -> tuple[MergeCandidate | None, ...]:
    try:
        with merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            target_lines,
        ) as _buffer:
            pass
        return (None,)
    except AtomicUnitError:
        raise
    except MergeError:
        pass

    candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
        source_lines,
        ownership,
        target_lines,
        max_candidates=_MAX_OPERATION_CANDIDATES,
    )
    if candidate_set.candidates:
        return candidate_set.candidates
    raise MergeError("Batch was created from a different version of the file")


def _materialize_target_candidate(
    *,
    target: _CandidateTarget,
    file_path: str,
    source_lines: LineBuffer,
    ownership,
    before_lines: LineBuffer,
    candidate: MergeCandidate | None,
    file_mode: str | None,
    text_change_type,
    destination_exists: bool,
    selected_ids: set[int] | None,
) -> _TargetCandidatePreview:
    before_copy = before_lines.clone()
    after = merge_batch_from_line_sequences_as_buffer(
        source_lines,
        ownership,
        before_lines,
        resolution=None if candidate is None else candidate.resolution,
    )
    return _TargetCandidatePreview(
        target=target,
        file_path=file_path,
        before_buffer=before_copy,
        after_buffer=after,
        file_mode=file_mode,
        change_type=selected_text_target_change_type(
            text_change_type,
            selected_ids,
            after,
        ).value,
        destination_exists=destination_exists,
        resolution=None if candidate is None else candidate.resolution,
        resolution_ordinal=1 if candidate is None else candidate.ordinal,
        resolution_count=1 if candidate is None else candidate.count,
        summary="unambiguous" if candidate is None else candidate.summary,
        explanation="" if candidate is None else candidate.explanation,
        ambiguity_target_line_range=(
            None if candidate is None else candidate.ambiguity_target_line_range
        ),
    )


def build_apply_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    source_lines: LineBuffer,
    ownership,
    worktree_lines: LineBuffer,
    batch_source_commit: str,
    file_meta: dict,
    text_change_type,
    worktree_file_mode: str | None,
    worktree_exists: bool,
    selected_ids: set[int] | None,
    selection_ids: set[int] | None,
) -> tuple[_OperationCandidatePreview, ...]:
    """Return apply candidates for a single file, or an empty tuple."""
    merge_candidates = _merge_candidates_or_unambiguous(
        source_lines,
        ownership,
        worktree_lines,
    )
    if merge_candidates == (None,):
        return ()

    batch_fingerprint = _fingerprint_batch(
        batch_name=batch_name,
        file_path=file_path,
        source_buffer=source_lines,
        ownership=ownership,
        batch_source_commit=batch_source_commit,
        file_meta=file_meta,
    )
    scope_fingerprint = _fingerprint_scope(
        operation="apply",
        batch_name=batch_name,
        file_path=file_path,
        selection_ids=selection_ids,
    )
    count = len(merge_candidates)
    previews: list[_OperationCandidatePreview] = []
    for ordinal, merge_candidate in enumerate(merge_candidates, start=1):
        target_preview = _materialize_target_candidate(
            target="worktree",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=worktree_lines,
            candidate=merge_candidate,
            file_mode=worktree_file_mode,
            text_change_type=text_change_type,
            destination_exists=worktree_exists,
            selected_ids=selected_ids,
        )
        target_fingerprints = {
            "worktree": _fingerprint_target(
                "worktree",
                file_path,
                target_preview.before_buffer,
            )
        }
        target_result_fingerprints = {
            "worktree": _fingerprint_target_result(target_preview)
        }
        targets = (target_preview,)
        preview = _OperationCandidatePreview(
            operation="apply",
            batch_name=batch_name,
            file_path=file_path,
            ordinal=ordinal,
            count=count,
            candidate_id="",
            targets=targets,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            target_result_fingerprints=target_result_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _fingerprint_candidate_id(
            operation="apply",
            batch_name=batch_name,
            file_path=file_path,
            scope_fingerprint=scope_fingerprint,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            target_result_fingerprints=target_result_fingerprints,
            targets=targets,
        )
        previews.append(preview)
    return tuple(previews)


def build_include_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    source_lines: LineBuffer,
    ownership,
    index_lines: LineBuffer,
    worktree_lines: LineBuffer,
    batch_source_commit: str,
    file_meta: dict,
    text_change_type,
    index_file_mode: str | None,
    worktree_file_mode: str | None,
    index_exists: bool,
    worktree_exists: bool,
    selected_ids: set[int] | None,
    selection_ids: set[int] | None,
    replacement_payload: ReplacementPayload | None = None,
) -> tuple[_OperationCandidatePreview, ...]:
    """Return include candidates for a single file, or an empty tuple."""
    index_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, index_lines)
    worktree_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, worktree_lines)
    if index_candidates == (None,) and worktree_candidates == (None,):
        return ()

    products = list(product(index_candidates, worktree_candidates))
    if len(products) > _MAX_OPERATION_CANDIDATES:
        raise _CandidateEnumerationLimitError("too many include candidates to preview safely")

    batch_fingerprint = _fingerprint_batch(
        batch_name=batch_name,
        file_path=file_path,
        source_buffer=source_lines,
        ownership=ownership,
        batch_source_commit=batch_source_commit,
        file_meta=file_meta,
    )
    scope_fingerprint = _fingerprint_scope(
        operation="include",
        batch_name=batch_name,
        file_path=file_path,
        selection_ids=selection_ids,
        replacement_payload=replacement_payload,
    )
    count = len(products)
    previews: list[_OperationCandidatePreview] = []
    for ordinal, (index_candidate, worktree_candidate) in enumerate(products, start=1):
        index_preview = _materialize_target_candidate(
            target="index",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=index_lines,
            candidate=index_candidate,
            file_mode=index_file_mode,
            text_change_type=text_change_type,
            destination_exists=index_exists,
            selected_ids=selected_ids,
        )
        worktree_preview = _materialize_target_candidate(
            target="worktree",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=worktree_lines,
            candidate=worktree_candidate,
            file_mode=worktree_file_mode,
            text_change_type=text_change_type,
            destination_exists=worktree_exists,
            selected_ids=selected_ids,
        )
        targets = (index_preview, worktree_preview)
        target_fingerprints = {
            "index": _fingerprint_target(
                "index",
                file_path,
                index_preview.before_buffer,
            ),
            "worktree": _fingerprint_target(
                "worktree",
                file_path,
                worktree_preview.before_buffer,
            ),
        }
        target_result_fingerprints = {
            "index": _fingerprint_target_result(index_preview),
            "worktree": _fingerprint_target_result(worktree_preview),
        }
        preview = _OperationCandidatePreview(
            operation="include",
            batch_name=batch_name,
            file_path=file_path,
            ordinal=ordinal,
            count=count,
            candidate_id="",
            targets=targets,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            target_result_fingerprints=target_result_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _fingerprint_candidate_id(
            operation="include",
            batch_name=batch_name,
            file_path=file_path,
            scope_fingerprint=scope_fingerprint,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            target_result_fingerprints=target_result_fingerprints,
            targets=targets,
        )
        previews.append(preview)
    return tuple(previews)
