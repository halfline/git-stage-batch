"""Persisted state for iterative fixup-suggestion searches."""

from __future__ import annotations

import json
from typing import TypedDict, cast

from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.paths import get_suggest_fixup_state_file_path


SUGGEST_FIXUP_STATE_SCHEMA_VERSION = 1


class SuggestFixupSearchState(TypedDict):
    """Canonical identity of one frozen suggestion search."""

    schema_version: int
    object_format: str
    hunk_hash: str
    line_id_ranges: list[list[int]] | None
    base_commit: str
    head_commit: str
    range_fingerprint: str
    file_path: str
    unit_id: str
    queried_ranges: list[list[int]]


class SuggestFixupState(SuggestFixupSearchState, total=False):
    """Validated persisted state for an iterative suggestion search."""

    last_shown_commit: str
    iteration: int


def _is_hex_identifier(value: object, lengths: tuple[int, ...]) -> bool:
    if not isinstance(value, str) or len(value) not in lengths:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _ranges_are_normalized(value: object, *, nullable: bool) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, list):
        return False

    previous_end: int | None = None
    for item in value:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and type(item[0]) is int
            and type(item[1]) is int
        ):
            return False
        start, end = item
        if (
            start <= 0
            or end < start
            or (previous_end is not None and start <= previous_end + 1)
        ):
            return False
        previous_end = end
    return True


def _state_is_valid(state: dict[object, object]) -> bool:
    line_id_ranges = state.get("line_id_ranges")
    queried_ranges = state.get("queried_ranges")
    has_iteration = (
        "last_shown_commit" in state or "iteration" in state
    )
    object_format = state.get("object_format")
    object_id_length = 40 if object_format == "sha1" else 64
    return (
        state.get("schema_version") == SUGGEST_FIXUP_STATE_SCHEMA_VERSION
        and object_format in {"sha1", "sha256"}
        and isinstance(state.get("hunk_hash"), str)
        and _ranges_are_normalized(line_id_ranges, nullable=True)
        and _is_hex_identifier(state.get("base_commit"), (object_id_length,))
        and _is_hex_identifier(state.get("head_commit"), (object_id_length,))
        and _is_hex_identifier(state.get("range_fingerprint"), (64,))
        and isinstance(state.get("file_path"), str)
        and _is_hex_identifier(state.get("unit_id"), (64,))
        and _ranges_are_normalized(queried_ranges, nullable=False)
        and (
            not has_iteration
            or (
                _is_hex_identifier(
                    state.get("last_shown_commit"),
                    (object_id_length,),
                )
                and type(state.get("iteration")) is int
                and cast(int, state.get("iteration")) > 0
            )
        )
    )


def read_suggest_fixup_state() -> SuggestFixupState | None:
    """Return persisted suggestion state, or None when absent or invalid."""
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
    if not _state_is_valid(state):
        return None
    return cast(SuggestFixupState, state)


def write_suggest_fixup_state(state: SuggestFixupState) -> None:
    """Persist canonical suggestion state."""
    get_suggest_fixup_state_file_path().parent.mkdir(parents=True, exist_ok=True)
    write_text_file_contents(
        get_suggest_fixup_state_file_path(),
        json.dumps(state, indent=2),
    )


def clear_suggest_fixup_state() -> None:
    """Remove persisted suggestion state."""
    get_suggest_fixup_state_file_path().unlink(missing_ok=True)


def suggest_fixup_state_matches_search(
    state: SuggestFixupState | None,
    search: SuggestFixupSearchState,
) -> bool:
    """Return whether state belongs to the exact frozen search."""
    if state is None:
        return False
    return all(state.get(key) == value for key, value in search.items())
