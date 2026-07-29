"""Persisted state for iterative suggest-fixup searches."""

from __future__ import annotations

import json
from typing import TypedDict, cast

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_suggest_fixup_state_file_path


class _SuggestFixupSearchState(TypedDict):
    hunk_hash: str
    line_ids: list[int] | None
    boundary: str
    file_path: str
    min_line: int
    max_line: int


class SuggestFixupState(_SuggestFixupSearchState, total=False):
    """Validated persisted state for an iterative suggest-fixup search."""

    last_shown_commit: str
    iteration: int


def read_suggest_fixup_state() -> SuggestFixupState | None:
    """Return persisted suggest-fixup state, or None when absent or invalid."""
    state_path = get_suggest_fixup_state_file_path()
    if not state_path.exists():
        return None
    try:
        value: object = json.loads(read_text_file_contents(state_path))
    except (json.JSONDecodeError, KeyError):
        return None
    if type(value) is not dict:
        return None
    state = cast(dict[object, object], value)
    line_ids = state.get("line_ids")
    if not (
        isinstance(state.get("hunk_hash"), str)
        and (
            line_ids is None
            or (
                isinstance(line_ids, list)
                and all(type(line_id) is int for line_id in line_ids)
            )
        )
        and isinstance(state.get("boundary"), str)
        and isinstance(state.get("file_path"), str)
        and type(state.get("min_line")) is int
        and type(state.get("max_line")) is int
        and (
            (
                "last_shown_commit" not in state
                and "iteration" not in state
            )
            or (
                isinstance(state.get("last_shown_commit"), str)
                and type(state.get("iteration")) is int
            )
        )
    ):
        return None
    return cast(SuggestFixupState, state)


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
