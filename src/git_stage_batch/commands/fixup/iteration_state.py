"""Suggest-fixup iteration state preparation."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from ...data.suggest_fixup_state import (
    SuggestFixupState,
    clear_suggest_fixup_state,
    read_suggest_fixup_state,
)
from ...i18n import _


@dataclass(frozen=True)
class SuggestFixupIterationContext:
    """Resolved suggest-fixup state for one command invocation."""

    effective_boundary: str | None
    state: SuggestFixupState | None


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

    effective_boundary = (
        state["base_commit"]
        if boundary is None and state is not None
        else boundary
    )

    if reset:
        clear_suggest_fixup_state()
        state = None

    return SuggestFixupIterationContext(
        effective_boundary=effective_boundary,
        state=state,
    )
