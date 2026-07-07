"""Fingerprints for page-aware file review state."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from ...core.buffer import LineBuffer
from ...core.models import ReviewActionGroup
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_working_tree_snapshot_file_path,
)
from ..line_state import (
    convert_line_changes_to_serializable_dict,
    load_line_changes_from_state,
)
from ..selected_change.store import SelectedChangeKind


def _json_hash(payload: Any) -> str:
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(data.encode("utf-8", errors="surrogateescape")).hexdigest()


def _hash_file(path: Path) -> str | None:
    if not path.exists():
        return None

    digest = sha256()
    with LineBuffer.from_path(path) as buffer:
        for chunk in buffer.byte_chunks():
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_selected_file_view(
    *,
    source: str,
    batch_name: str | None,
    file_path: str,
    selected_change_kind: SelectedChangeKind,
    gutter_to_selection_id: dict[int, int] | None = None,
    actionable_selection_groups: tuple[tuple[int, ...], ...] | None = None,
    review_action_groups: tuple[ReviewActionGroup, ...] | None = None,
    line_changes=None,
) -> str:
    """Fingerprint the selected file view and its current line ID space."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    snapshots = {}
    for name, path in (
        ("index", get_index_snapshot_file_path()),
        ("working_tree", get_working_tree_snapshot_file_path()),
    ):
        snapshots[name] = _hash_file(path)
    return _json_hash(
        {
            "source": source,
            "batch_name": batch_name,
            "file_path": file_path,
            "selected_change_kind": selected_change_kind.value,
            "snapshots": snapshots,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
            "gutter_to_selection_id": gutter_to_selection_id,
            "actionable_selection_groups": actionable_selection_groups,
            "review_action_groups": [
                {
                    "display_ids": group.display_ids,
                    "selection_ids": group.selection_ids,
                    "actions": group.actions,
                    "reason": group.reason,
                }
                for group in (review_action_groups or ())
            ],
        }
    )


def compute_current_file_review_diff_fingerprint(
    file_path: str,
    line_changes=None,
) -> str:
    """Fingerprint the cached selected file diff for freshness checks."""
    if line_changes is None:
        line_changes = load_line_changes_from_state()
    return _json_hash(
        {
            "file_path": file_path,
            "line_changes": (
                convert_line_changes_to_serializable_dict(line_changes)
                if line_changes is not None else None
            ),
        }
    )
