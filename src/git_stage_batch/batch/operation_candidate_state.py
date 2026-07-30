"""Persistent state for reviewed operation candidates."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, TypedDict, cast

from ..utils.file_io import write_text_file_contents
from ..utils.paths import get_batch_candidate_state_file_path
from .operation_candidate_fingerprints import ALGORITHM_VERSION

if TYPE_CHECKING:
    from .operation_candidate_types import OperationCandidatePreview


class CandidatePreviewState(TypedDict):
    """Persisted details for one reviewed candidate ordinal."""

    ordinal: int
    candidate_id: str
    target_fingerprints: dict[str, str]
    target_result_fingerprints: dict[str, str]


class CandidateScopeState(TypedDict):
    """Persisted candidate state for one operation/file scope."""

    batch_name: str
    operation: str
    file: str
    batch_fingerprint: str
    scope_fingerprint: str
    candidate_count: int
    previews: dict[str, CandidatePreviewState]


class OperationCandidateState(TypedDict):
    """Top-level persisted operation-candidate state."""

    schema_version: int
    algorithm_version: int
    scopes: dict[str, CandidateScopeState]


def _empty_state() -> OperationCandidateState:
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "scopes": {},
    }


def _load_state() -> OperationCandidateState:
    path = get_batch_candidate_state_file_path()
    if not path.exists():
        return _empty_state()
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(raw_data, dict):
        return _empty_state()
    data = cast(dict[str, object], raw_data)
    if data.get("schema_version") != 1:
        return _empty_state()
    if data.get("algorithm_version") != ALGORITHM_VERSION:
        return _empty_state()
    scopes = data.get("scopes")
    if not isinstance(scopes, dict) or not all(
        isinstance(key, str) and isinstance(scope, dict)
        for key, scope in scopes.items()
    ):
        return _empty_state()
    return cast(OperationCandidateState, data)


def _save_state(data: OperationCandidateState) -> None:
    path = get_batch_candidate_state_file_path()
    write_text_file_contents(path, json.dumps(data, indent=2, sort_keys=True))


def clear_candidate_preview_state_for_file(*, batch_name: str, file_path: str) -> None:
    """Remove saved candidate previews for one batch file."""
    data = _load_state()
    scopes = data["scopes"]
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
    scope_key = candidate_preview_scope_key(preview)
    scope = data["scopes"].get(scope_key)
    if scope is None:
        scope = {
            "batch_name": preview.batch_name,
            "operation": preview.operation,
            "file": preview.file_path,
            "batch_fingerprint": preview.batch_fingerprint,
            "scope_fingerprint": preview.scope_fingerprint,
            "candidate_count": preview.count,
            "previews": {},
        }
        data["scopes"][scope_key] = scope
    scope.update({
        "batch_name": preview.batch_name,
        "operation": preview.operation,
        "file": preview.file_path,
        "batch_fingerprint": preview.batch_fingerprint,
        "scope_fingerprint": preview.scope_fingerprint,
        "candidate_count": preview.count,
    })
    scope["previews"][str(preview.ordinal)] = {
        "ordinal": preview.ordinal,
        "candidate_id": preview.candidate_id,
        "target_fingerprints": preview.target_fingerprints,
        "target_result_fingerprints": preview.target_result_fingerprints,
    }
    _save_state(data)


def load_candidate_preview_state(
    preview: OperationCandidatePreview,
) -> CandidatePreviewState | None:
    scope = _load_state()["scopes"].get(candidate_preview_scope_key(preview))
    if scope is None:
        return None
    return scope["previews"].get(str(preview.ordinal))
