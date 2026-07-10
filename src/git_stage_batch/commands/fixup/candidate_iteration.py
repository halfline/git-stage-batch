"""Suggest-fixup candidate iteration helpers."""

from __future__ import annotations

from dataclasses import dataclass

from ...data.suggest_fixup_state import (
    clear_suggest_fixup_state,
    write_suggest_fixup_state,
)
from ...exceptions import exit_with_error
from ...i18n import _
from .history import find_next_fixup_candidate
from .search_state import SuggestFixupSearchTarget


@dataclass(frozen=True)
class SuggestFixupCandidate:
    """Resolved suggest-fixup candidate for one iteration."""

    commit: str
    iteration: int


def advance_suggest_fixup_candidate(
    *,
    state: dict | None,
    target: SuggestFixupSearchTarget,
) -> SuggestFixupCandidate:
    """Find, persist, and return the next suggest-fixup candidate."""
    last_shown = state["last_shown_commit"] if state else None
    iteration = state["iteration"] + 1 if state else 1

    candidate_commit = find_next_fixup_candidate(
        target.file_path,
        target.min_line,
        target.max_line,
        target.boundary,
        last_shown,
    )

    if not candidate_commit:
        if iteration == 1:
            exit_with_error(
                f"No commits in range {target.boundary}..HEAD modified these lines.\n"
                + "The changes may be fixing code from before the boundary."
            )

        clear_suggest_fixup_state()
        exit_with_error(_("No more candidates found."))

    write_suggest_fixup_state(
        {
            "hunk_hash": target.hunk_hash,
            "line_ids": target.line_ids,
            "boundary": target.boundary,
            "file_path": target.file_path,
            "min_line": target.min_line,
            "max_line": target.max_line,
            "last_shown_commit": candidate_commit,
            "iteration": iteration,
        }
    )

    return SuggestFixupCandidate(
        commit=candidate_commit,
        iteration=iteration,
    )
