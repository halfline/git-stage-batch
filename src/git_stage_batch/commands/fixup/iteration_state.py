"""Suggest-fixup iteration state preparation."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from ...data.suggest_fixup_state import (
    clear_suggest_fixup_state,
    read_suggest_fixup_state,
)
from ...i18n import _


@dataclass(frozen=True)
class SuggestFixupIterationContext:
    """Resolved suggest-fixup state for one command invocation."""

    effective_boundary: str
    state: dict | None


def prepare_suggest_fixup_iteration(
    *,
    boundary: str | None,
    reset: bool,
    abort: bool,
    porcelain: bool,
) -> SuggestFixupIterationContext | None:
    """Resolve persisted suggest-fixup state and effective boundary."""
    if abort:
        clear_suggest_fixup_state()
        if not porcelain:
            print(_("Suggest-fixup iteration cleared."), file=sys.stderr)
        return None

    state = read_suggest_fixup_state()

    if boundary is None:
        effective_boundary = state.get("boundary") if state else "@{upstream}"
    else:
        effective_boundary = boundary
        if state and state.get("boundary") != boundary:
            clear_suggest_fixup_state()
            state = None

    if reset:
        clear_suggest_fixup_state()
        state = None

    return SuggestFixupIterationContext(
        effective_boundary=effective_boundary,
        state=state,
    )
