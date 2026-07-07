"""Persisted state for iterative suggest-fixup searches."""

from __future__ import annotations

import json
from typing import Any

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_suggest_fixup_state_file_path


SuggestFixupState = dict[str, Any]


def read_suggest_fixup_state() -> SuggestFixupState | None:
    """Return persisted suggest-fixup state, or None when absent or invalid."""
    state_path = get_suggest_fixup_state_file_path()
    if not state_path.exists():
        return None
    try:
        return json.loads(read_text_file_contents(state_path))
    except (json.JSONDecodeError, KeyError):
        return None


def write_suggest_fixup_state(state: SuggestFixupState) -> None:
    """Persist suggest-fixup state."""
    write_text_file_contents(
        get_suggest_fixup_state_file_path(),
        json.dumps(state, indent=2),
    )


def clear_suggest_fixup_state() -> None:
    """Remove persisted suggest-fixup state."""
    get_suggest_fixup_state_file_path().unlink(missing_ok=True)


def suggest_fixup_state_should_reset(
    selected_hunk_hash: str,
    line_ids: list[int] | None,
    boundary: str,
    file_path: str,
    min_line: int,
    max_line: int,
) -> bool:
    """Return whether persisted state belongs to a different search context."""
    state = read_suggest_fixup_state()
    if state is None:
        return True

    return (
        state.get("hunk_hash") != selected_hunk_hash
        or state.get("line_ids") != line_ids
        or state.get("boundary") != boundary
        or state.get("file_path") != file_path
        or state.get("min_line") != min_line
        or state.get("max_line") != max_line
    )
