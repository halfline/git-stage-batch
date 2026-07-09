"""Persistent state for reviewed operation candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..utils.paths import get_batch_candidate_state_file_path
from .operation_candidate_fingerprints import ALGORITHM_VERSION

if TYPE_CHECKING:
    from .operation_candidates import OperationCandidatePreview


def _empty_state() -> dict:
    return {
        "schema_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "scopes": {},
    }


def _load_state() -> dict:
    path = get_batch_candidate_state_file_path()
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if data.get("schema_version") != 1:
        return _empty_state()
    if data.get("algorithm_version") != ALGORITHM_VERSION:
        return _empty_state()
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
