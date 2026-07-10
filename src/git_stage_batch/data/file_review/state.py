"""Persisted safety state for page-aware file reviews."""

from __future__ import annotations

import json
from dataclasses import asdict

from ...core.actionable_changes import ActionableSelectionReason
from ...utils.file_io import read_text_file_contents, write_text_file_contents
from ...utils.paths import get_last_file_review_state_file_path
from . import records as _records
from ..selected_change.store import SelectedChangeKind


def write_last_file_review_state(review_state: _records.FileReviewState) -> None:
    """Persist the last file review state."""
    write_text_file_contents(
        get_last_file_review_state_file_path(),
        json.dumps(asdict(review_state), ensure_ascii=False, indent=0),
    )


def read_last_file_review_state(
    *,
    clear_invalid: bool = True,
) -> _records.FileReviewState | None:
    """Read the last file review state, optionally clearing invalid state."""
    path = get_last_file_review_state_file_path()
    if not path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(path))
        selections = tuple(
            _records.FileReviewSelectionState(
                display_ids=tuple(selection["display_ids"]),
                selection_ids=tuple(selection["selection_ids"]),
                change_index=selection["change_index"],
                first_page=selection["first_page"],
                last_page=selection["last_page"],
                reason=ActionableSelectionReason(selection["reason"]),
                actions=tuple(
                    _records.coerce_review_action(action)
                    for action in selection["actions"]
                ),
                is_splittable=bool(selection["is_splittable"]),
            )
            for selection in data.get("selections", [])
        )
        return _records.FileReviewState(
            source=_records.coerce_review_source(data["source"]),
            batch_name=data.get("batch_name"),
            file_path=data["file_path"],
            page_spec=data["page_spec"],
            shown_pages=tuple(data["shown_pages"]),
            page_count=data["page_count"],
            entire_file_shown=data["entire_file_shown"],
            selections=selections,
            selected_change_kind=SelectedChangeKind(data["selected_change_kind"]),
            selected_file_fingerprint=data["selected_file_fingerprint"],
            diff_fingerprint=data["diff_fingerprint"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        if clear_invalid:
            clear_last_file_review_state()
        return None


def clear_last_file_review_state() -> None:
    """Remove the last file review state."""
    get_last_file_review_state_file_path().unlink(missing_ok=True)


def clear_last_file_review_state_if_file_matches(file_path: str) -> None:
    """Remove the last file review state if it belongs to the given file."""
    review_state = read_last_file_review_state()
    if review_state is not None and review_state.file_path == file_path:
        clear_last_file_review_state()
