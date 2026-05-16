"""Operation-level candidate planning and state for batch apply/include."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from itertools import product
from typing import Literal

from .merge import (
    MergeError,
    MergeCandidate,
    MergeResolution,
    enumerate_merge_batch_candidates_from_line_sequences,
    merge_batch_from_line_sequences_as_buffer,
)
from .replacement import ReplacementPayload
from ..editor import EditorBuffer
from ..exceptions import AtomicUnitError


CandidateOperation = Literal["apply", "include"]
CandidateTarget = Literal["index", "worktree"]
MAX_OPERATION_CANDIDATES = 50
ALGORITHM_VERSION = 1


class CandidateEnumerationLimitError(ValueError):
    """Raised when a candidate set is too large to preview safely."""


@dataclass
class TargetCandidatePreview:
    """Materialized candidate result for one target."""

    target: CandidateTarget
    file_path: str
    before_buffer: EditorBuffer
    after_buffer: EditorBuffer
    resolution: MergeResolution | None
    resolution_ordinal: int
    resolution_count: int
    summary: str
    explanation: str

    def close(self) -> None:
        self.before_buffer.close()
        self.after_buffer.close()


@dataclass
class OperationCandidatePreview:
    """Materialized preview for one complete operation candidate."""

    operation: CandidateOperation
    batch_name: str
    file_path: str
    ordinal: int
    count: int
    candidate_id: str
    targets: tuple[TargetCandidatePreview, ...]
    batch_fingerprint: str
    target_fingerprints: dict[str, str]
    scope_fingerprint: str

    def close(self) -> None:
        for target in self.targets:
            target.close()


def _buffer_bytes(buffer: EditorBuffer) -> bytes:
    return buffer.to_bytes()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target_fingerprint(target: CandidateTarget, file_path: str, buffer: EditorBuffer) -> str:
    return _hash_bytes(
        b"\0".join(
            [
                target.encode(),
                file_path.encode("utf-8", errors="surrogateescape"),
                _buffer_bytes(buffer),
            ]
        )
    )


def _candidate_id(
    *,
    operation: CandidateOperation,
    batch_name: str,
    file_path: str,
    scope_fingerprint: str,
    batch_fingerprint: str,
    target_fingerprints: dict[str, str],
    targets: tuple[TargetCandidatePreview, ...],
) -> str:
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "operation": operation,
        "batch_name": batch_name,
        "file": file_path,
        "scope": scope_fingerprint,
        "batch_fingerprint": batch_fingerprint,
        "targets": target_fingerprints,
        "decisions": [
            [
                target.target,
                None if target.resolution is None else sorted(target.resolution.decisions.items()),
            ]
            for target in targets
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def _scope_fingerprint(
    *,
    operation: CandidateOperation,
    batch_name: str,
    file_path: str,
    selection_ids: set[int] | None,
    replacement_payload: ReplacementPayload | None = None,
) -> str:
    payload = {
        "operation": operation,
        "batch": batch_name,
        "file": file_path,
        "selection_ids": sorted(selection_ids) if selection_ids is not None else None,
        "replacement": (
            _hash_bytes(replacement_payload.data)
            if replacement_payload is not None
            else None
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _batch_fingerprint(batch_name: str, file_path: str, source_buffer: EditorBuffer) -> str:
    return _hash_bytes(
        b"\0".join(
            [
                batch_name.encode("utf-8", errors="surrogateescape"),
                file_path.encode("utf-8", errors="surrogateescape"),
                _buffer_bytes(source_buffer),
            ]
        )
    )


def _merge_candidates_or_unambiguous(
    source_lines: EditorBuffer,
    ownership,
    target_lines: EditorBuffer,
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
        max_candidates=MAX_OPERATION_CANDIDATES,
    )
    if candidate_set.candidates:
        return candidate_set.candidates
    raise MergeError("Batch was created from a different version of the file")


def _materialize_target_candidate(
    *,
    target: CandidateTarget,
    file_path: str,
    source_lines: EditorBuffer,
    ownership,
    before_lines: EditorBuffer,
    candidate: MergeCandidate | None,
) -> TargetCandidatePreview:
    before_copy = EditorBuffer.from_bytes(before_lines.to_bytes())
    after = merge_batch_from_line_sequences_as_buffer(
        source_lines,
        ownership,
        before_lines,
        resolution=None if candidate is None else candidate.resolution,
    )
    return TargetCandidatePreview(
        target=target,
        file_path=file_path,
        before_buffer=before_copy,
        after_buffer=after,
        resolution=None if candidate is None else candidate.resolution,
        resolution_ordinal=1 if candidate is None else candidate.ordinal,
        resolution_count=1 if candidate is None else candidate.count,
        summary="unambiguous" if candidate is None else candidate.summary,
        explanation="" if candidate is None else candidate.explanation,
    )


def build_apply_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    source_lines: EditorBuffer,
    ownership,
    worktree_lines: EditorBuffer,
    selection_ids: set[int] | None,
) -> tuple[OperationCandidatePreview, ...]:
    """Return apply candidates for a single file, or an empty tuple."""
    merge_candidates = _merge_candidates_or_unambiguous(
        source_lines,
        ownership,
        worktree_lines,
    )
    if merge_candidates == (None,):
        return ()

    batch_fingerprint = _batch_fingerprint(batch_name, file_path, source_lines)
    scope_fingerprint = _scope_fingerprint(
        operation="apply",
        batch_name=batch_name,
        file_path=file_path,
        selection_ids=selection_ids,
    )
    count = len(merge_candidates)
    previews: list[OperationCandidatePreview] = []
    for ordinal, merge_candidate in enumerate(merge_candidates, start=1):
        target_preview = _materialize_target_candidate(
            target="worktree",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=worktree_lines,
            candidate=merge_candidate,
        )
        target_fingerprints = {
            "worktree": _target_fingerprint("worktree", file_path, target_preview.before_buffer)
        }
        targets = (target_preview,)
        preview = OperationCandidatePreview(
            operation="apply",
            batch_name=batch_name,
            file_path=file_path,
            ordinal=ordinal,
            count=count,
            candidate_id="",
            targets=targets,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _candidate_id(
            operation="apply",
            batch_name=batch_name,
            file_path=file_path,
            scope_fingerprint=scope_fingerprint,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            targets=targets,
        )
        previews.append(preview)
    return tuple(previews)


def build_include_candidate_previews(
    *,
    batch_name: str,
    file_path: str,
    source_lines: EditorBuffer,
    ownership,
    index_lines: EditorBuffer,
    worktree_lines: EditorBuffer,
    selection_ids: set[int] | None,
    replacement_payload: ReplacementPayload | None = None,
) -> tuple[OperationCandidatePreview, ...]:
    """Return include candidates for a single file, or an empty tuple."""
    index_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, index_lines)
    worktree_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, worktree_lines)
    if index_candidates == (None,) and worktree_candidates == (None,):
        return ()

    products = list(product(index_candidates, worktree_candidates))
    if len(products) > MAX_OPERATION_CANDIDATES:
        raise CandidateEnumerationLimitError("too many include candidates to preview safely")

    batch_fingerprint = _batch_fingerprint(batch_name, file_path, source_lines)
    scope_fingerprint = _scope_fingerprint(
        operation="include",
        batch_name=batch_name,
        file_path=file_path,
        selection_ids=selection_ids,
        replacement_payload=replacement_payload,
    )
    count = len(products)
    previews: list[OperationCandidatePreview] = []
    for ordinal, (index_candidate, worktree_candidate) in enumerate(products, start=1):
        index_preview = _materialize_target_candidate(
            target="index",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=index_lines,
            candidate=index_candidate,
        )
        worktree_preview = _materialize_target_candidate(
            target="worktree",
            file_path=file_path,
            source_lines=source_lines,
            ownership=ownership,
            before_lines=worktree_lines,
            candidate=worktree_candidate,
        )
        targets = (index_preview, worktree_preview)
        target_fingerprints = {
            "index": _target_fingerprint("index", file_path, index_preview.before_buffer),
            "worktree": _target_fingerprint("worktree", file_path, worktree_preview.before_buffer),
        }
        preview = OperationCandidatePreview(
            operation="include",
            batch_name=batch_name,
            file_path=file_path,
            ordinal=ordinal,
            count=count,
            candidate_id="",
            targets=targets,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _candidate_id(
            operation="include",
            batch_name=batch_name,
            file_path=file_path,
            scope_fingerprint=scope_fingerprint,
            batch_fingerprint=batch_fingerprint,
            target_fingerprints=target_fingerprints,
            targets=targets,
        )
        previews.append(preview)
    return tuple(previews)


def render_candidate_buffer_diff(
    file_path: str,
    before_buffer: EditorBuffer,
    after_buffer: EditorBuffer,
    *,
    label_before: str,
    label_after: str,
    context_lines: int,
) -> str:
    """Render a unified diff between two candidate buffers."""
    before_text = before_buffer.to_bytes().decode("utf-8", errors="surrogateescape")
    after_text = after_buffer.to_bytes().decode("utf-8", errors="surrogateescape")
    return "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"{label_before}/{file_path}",
            tofile=f"{label_after}/{file_path}",
            n=context_lines,
        )
    )
