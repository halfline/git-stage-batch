"""Operation-level candidate planning and state for batch apply/include."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from typing import Literal

from ..core.text_lifecycle import selected_text_target_change_type
from ..editor import EditorBuffer, buffer_byte_chunks
from ..exceptions import AtomicUnitError
from ..utils.paths import get_batch_candidate_state_file_path
from .merge import (
    MergeError,
    MergeCandidate,
    MergeResolution,
    enumerate_merge_batch_candidates_from_line_sequences,
    merge_batch_from_line_sequences_as_buffer,
)
from .replacement import ReplacementPayload


CandidateOperation = Literal["apply", "include"]
CandidateTarget = Literal["index", "worktree"]
MAX_OPERATION_CANDIDATES = 50
ALGORITHM_VERSION = 2


class CandidateEnumerationLimitError(ValueError):
    """Raised when a candidate set is too large to preview safely."""


@dataclass
class TargetCandidatePreview:
    """Materialized candidate result for one target."""

    target: CandidateTarget
    file_path: str
    before_buffer: EditorBuffer
    after_buffer: EditorBuffer
    file_mode: str | None
    change_type: str
    destination_exists: bool
    resolution: MergeResolution | None
    resolution_ordinal: int
    resolution_count: int
    summary: str
    explanation: str
    ambiguity_target_line_range: tuple[int, int] | None

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
    target_result_fingerprints: dict[str, str]
    scope_fingerprint: str

    def close(self) -> None:
        for target in self.targets:
            target.close()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _buffer_fingerprint(buffer) -> str:
    digest = hashlib.sha256()
    for chunk in buffer_byte_chunks(buffer):
        digest.update(chunk)
    return digest.hexdigest()


def _json_fingerprint(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _target_fingerprint(target: CandidateTarget, file_path: str, buffer: EditorBuffer) -> str:
    return _json_fingerprint({
        "target": target,
        "file": file_path,
        "buffer": _buffer_fingerprint(buffer),
    })


def _target_result_fingerprint(target: TargetCandidatePreview) -> str:
    return _json_fingerprint({
        "target": target.target,
        "file": target.file_path,
        "before": _buffer_fingerprint(target.before_buffer),
        "after": _buffer_fingerprint(target.after_buffer),
        "file_mode": target.file_mode,
        "change_type": target.change_type,
        "destination_exists": target.destination_exists,
    })


def _baseline_reference_payload(reference) -> dict | None:
    if reference is None:
        return None
    return {
        "after_line": reference.after_line,
        "after_content": (
            None if reference.after_content is None else _hash_bytes(reference.after_content)
        ),
        "has_after_line": reference.has_after_line,
        "before_line": reference.before_line,
        "before_content": (
            None if reference.before_content is None else _hash_bytes(reference.before_content)
        ),
        "has_before_line": reference.has_before_line,
    }


def _absence_claim_payload(claim) -> dict:
    return {
        "anchor_line": claim.anchor_line,
        "content": _buffer_fingerprint(claim.content_lines),
        "line_count": len(claim.content_lines),
        "baseline_reference": _baseline_reference_payload(claim.baseline_reference),
    }


def _presence_claim_payload(claim) -> dict:
    return {
        "source_lines": claim.source_lines,
        "baseline_references": [
            [line, _baseline_reference_payload(reference)]
            for line, reference in sorted(claim.baseline_references.items())
        ],
    }


def _replacement_unit_payload(unit) -> dict:
    return {
        "presence_lines": unit.presence_lines,
        "deletion_indices": unit.deletion_indices,
    }


def _ownership_fingerprint(ownership) -> str:
    return _json_fingerprint({
        "presence_claims": [
            _presence_claim_payload(claim)
            for claim in ownership.presence_claims
        ],
        "deletions": [
            _absence_claim_payload(claim)
            for claim in ownership.deletions
        ],
        "replacement_units": [
            _replacement_unit_payload(unit)
            for unit in ownership.replacement_units
        ],
    })


def _candidate_id(
    *,
    operation: CandidateOperation,
    batch_name: str,
    file_path: str,
    scope_fingerprint: str,
    batch_fingerprint: str,
    target_fingerprints: dict[str, str],
    target_result_fingerprints: dict[str, str],
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
        "target_results": target_result_fingerprints,
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


def _batch_fingerprint(
    *,
    batch_name: str,
    file_path: str,
    source_buffer: EditorBuffer,
    ownership,
    batch_source_commit: str,
    file_meta: dict,
) -> str:
    return _json_fingerprint({
        "algorithm_version": ALGORITHM_VERSION,
        "batch_name": batch_name,
        "file": file_path,
        "batch_source_commit": batch_source_commit,
        "source": _buffer_fingerprint(source_buffer),
        "file_metadata": file_meta,
        "ownership": _ownership_fingerprint(ownership),
    })


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
    file_mode: str | None,
    text_change_type,
    destination_exists: bool,
    selected_ids: set[int] | None,
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
    source_lines: EditorBuffer,
    ownership,
    worktree_lines: EditorBuffer,
    batch_source_commit: str,
    file_meta: dict,
    text_change_type,
    worktree_file_mode: str | None,
    worktree_exists: bool,
    selected_ids: set[int] | None,
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

    batch_fingerprint = _batch_fingerprint(
        batch_name=batch_name,
        file_path=file_path,
        source_buffer=source_lines,
        ownership=ownership,
        batch_source_commit=batch_source_commit,
        file_meta=file_meta,
    )
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
            file_mode=worktree_file_mode,
            text_change_type=text_change_type,
            destination_exists=worktree_exists,
            selected_ids=selected_ids,
        )
        target_fingerprints = {
            "worktree": _target_fingerprint("worktree", file_path, target_preview.before_buffer)
        }
        target_result_fingerprints = {
            "worktree": _target_result_fingerprint(target_preview)
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
            target_result_fingerprints=target_result_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _candidate_id(
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
    source_lines: EditorBuffer,
    ownership,
    index_lines: EditorBuffer,
    worktree_lines: EditorBuffer,
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
) -> tuple[OperationCandidatePreview, ...]:
    """Return include candidates for a single file, or an empty tuple."""
    index_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, index_lines)
    worktree_candidates = _merge_candidates_or_unambiguous(source_lines, ownership, worktree_lines)
    if index_candidates == (None,) and worktree_candidates == (None,):
        return ()

    products = list(product(index_candidates, worktree_candidates))
    if len(products) > MAX_OPERATION_CANDIDATES:
        raise CandidateEnumerationLimitError("too many include candidates to preview safely")

    batch_fingerprint = _batch_fingerprint(
        batch_name=batch_name,
        file_path=file_path,
        source_buffer=source_lines,
        ownership=ownership,
        batch_source_commit=batch_source_commit,
        file_meta=file_meta,
    )
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
            "index": _target_fingerprint("index", file_path, index_preview.before_buffer),
            "worktree": _target_fingerprint("worktree", file_path, worktree_preview.before_buffer),
        }
        target_result_fingerprints = {
            "index": _target_result_fingerprint(index_preview),
            "worktree": _target_result_fingerprint(worktree_preview),
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
            target_result_fingerprints=target_result_fingerprints,
            scope_fingerprint=scope_fingerprint,
        )
        preview.candidate_id = _candidate_id(
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


def _load_state() -> dict:
    path = get_batch_candidate_state_file_path()
    if not path.exists():
        return {"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "scopes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "scopes": {}}
    if data.get("schema_version") != 1:
        return {"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "scopes": {}}
    if data.get("algorithm_version") != ALGORITHM_VERSION:
        return {"schema_version": 1, "algorithm_version": ALGORITHM_VERSION, "scopes": {}}
    data.setdefault("scopes", {})
    return data


def _save_state(data: dict) -> None:
    path = get_batch_candidate_state_file_path()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def clear_candidate_preview_state_for_file(*, batch_name: str, file_path: str) -> None:
    """Remove saved candidate previews for one batch file."""
    data = _load_state()
    scopes = data.get("scopes", {})
    matching_keys = [
        key
        for key, scope in scopes.items()
        if scope.get("batch_name") == batch_name and scope.get("file") == file_path
    ]
    if not matching_keys:
        return

    for key in matching_keys:
        del scopes[key]

    if scopes:
        _save_state(data)
        return

    get_batch_candidate_state_file_path().unlink(missing_ok=True)


def candidate_preview_scope_key(preview: OperationCandidatePreview) -> str:
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "operation": preview.operation,
        "batch": preview.batch_name,
        "file": preview.file_path,
        "scope": preview.scope_fingerprint,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"{preview.operation}:{preview.batch_name}:{preview.file_path}:{digest}"


def save_candidate_preview_state(preview: OperationCandidatePreview) -> None:
    data = _load_state()
    data["algorithm_version"] = ALGORITHM_VERSION
    scope = data["scopes"].setdefault(candidate_preview_scope_key(preview), {})
    scope.update({
        "batch_name": preview.batch_name,
        "operation": preview.operation,
        "file": preview.file_path,
        "batch_fingerprint": preview.batch_fingerprint,
        "scope_fingerprint": preview.scope_fingerprint,
        "candidate_count": preview.count,
    })
    scope.setdefault("previews", {})[str(preview.ordinal)] = {
        "ordinal": preview.ordinal,
        "candidate_id": preview.candidate_id,
        "target_fingerprints": preview.target_fingerprints,
        "target_result_fingerprints": preview.target_result_fingerprints,
        "shown_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(data)


def load_candidate_preview_state(preview: OperationCandidatePreview) -> dict | None:
    scope = _load_state().get("scopes", {}).get(candidate_preview_scope_key(preview))
    if scope is None:
        return None
    return scope.get("previews", {}).get(str(preview.ordinal))
