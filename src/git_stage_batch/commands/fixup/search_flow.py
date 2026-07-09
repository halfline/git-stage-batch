"""Suggest-fixup search execution flow."""

from __future__ import annotations

from .boundary import require_suggest_fixup_boundary_range
from .candidate_display import (
    display_suggest_fixup_candidate,
    show_last_suggest_fixup_candidate,
)
from .candidate_iteration import advance_suggest_fixup_candidate
from .search_state import reset_suggest_fixup_state_for_search
from .search_targets import SuggestFixupResolvedTarget


def run_suggest_fixup_search(
    *,
    state: dict | None,
    resolved_target: SuggestFixupResolvedTarget,
    show_last: bool,
    porcelain: bool,
) -> None:
    """Run suggest-fixup candidate search for a resolved target."""
    line_changes = resolved_target.line_changes
    search_target = resolved_target.search_target

    require_suggest_fixup_boundary_range(search_target.boundary)

    state = reset_suggest_fixup_state_for_search(
        state=state,
        target=search_target,
    )

    if show_last:
        show_last_suggest_fixup_candidate(
            state=state,
            effective_boundary=search_target.boundary,
            file_path=line_changes.path,
            porcelain=porcelain,
        )
        return

    candidate = advance_suggest_fixup_candidate(
        state=state,
        target=search_target,
    )

    display_suggest_fixup_candidate(
        candidate_commit=candidate.commit,
        iteration=candidate.iteration,
        boundary=search_target.boundary,
        file_path=line_changes.path,
        porcelain=porcelain,
    )
