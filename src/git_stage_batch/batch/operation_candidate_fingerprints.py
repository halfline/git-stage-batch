"""Stable fingerprints for operation candidate previews."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from ..core.buffer import buffer_byte_chunks

if TYPE_CHECKING:
    from ..core.buffer import LineBuffer
    from ..core.replacement import ReplacementPayload
    from .operation_candidates import (
        CandidateOperation,
        CandidateTarget,
        TargetCandidatePreview,
    )


ALGORITHM_VERSION = 2


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


def target_fingerprint(
    target: CandidateTarget,
    file_path: str,
    buffer: LineBuffer,
) -> str:
    return _json_fingerprint({
        "target": target,
        "file": file_path,
        "buffer": _buffer_fingerprint(buffer),
    })


def target_result_fingerprint(target: TargetCandidatePreview) -> str:
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
            None
            if reference.after_content is None
            else _hash_bytes(reference.after_content)
        ),
        "has_after_line": reference.has_after_line,
        "before_line": reference.before_line,
        "before_content": (
            None
            if reference.before_content is None
            else _hash_bytes(reference.before_content)
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
    origin = unit.origin
    return {
        "presence_lines": unit.presence_lines,
        "deletion_indices": unit.deletion_indices,
        "origin": None if origin is None else {
            "old_start": origin.old_start,
            "old_end": origin.old_end,
            "new_start": origin.new_start,
            "new_end": origin.new_end,
            "baseline_reference": _baseline_reference_payload(
                origin.baseline_reference
            ),
        },
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


def candidate_id(
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
                None
                if target.resolution is None
                else sorted(target.resolution.decisions.items()),
            ]
            for target in targets
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:12]


def scope_fingerprint(
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


def batch_fingerprint(
    *,
    batch_name: str,
    file_path: str,
    source_buffer: LineBuffer,
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
