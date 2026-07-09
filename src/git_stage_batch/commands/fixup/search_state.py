"""Suggest-fixup search state preparation."""

from __future__ import annotations

from dataclasses import dataclass

from ...data.suggest_fixup_state import (
    clear_suggest_fixup_state,
    suggest_fixup_state_should_reset,
)


@dataclass(frozen=True)
class SuggestFixupSearchTarget:
    """Resolved suggest-fixup search target for persisted state checks."""

    hunk_hash: str
    line_ids: list[int] | None
    boundary: str
    file_path: str
    min_line: int
    max_line: int


def reset_suggest_fixup_state_for_search(
    *,
    state: dict | None,
    target: SuggestFixupSearchTarget,
) -> dict | None:
    """Return state after clearing stale data for a changed search target."""
    if state and suggest_fixup_state_should_reset(
        target.hunk_hash,
        target.line_ids,
        target.boundary,
        target.file_path,
        target.min_line,
        target.max_line,
    ):
        clear_suggest_fixup_state()
        return None

    return state
