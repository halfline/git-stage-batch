"""Deterministic iteration over fixup-suggestion candidates."""

from __future__ import annotations

from ...data.suggest_fixup_state import (
    SuggestFixupState,
    write_suggest_fixup_state,
)
from ...fixup.models import (
    LineageHistoryEvidence,
    PlacementEvidence,
    SuggestFixupCandidate,
    SuggestFixupCandidateSource,
)
from .search_state import SuggestFixupSearchTarget


def suggest_fixup_candidate_commits(
    *,
    target: SuggestFixupSearchTarget,
    history: LineageHistoryEvidence,
    placement: PlacementEvidence,
) -> tuple[str, ...]:
    """Return every evidenced candidate in canonical newest-first order."""
    history_candidates = set(history.candidates)
    return tuple(
        commit
        for commit in target.commit_range.commits_newest_first
        if commit in history_candidates or commit == placement.barrier
    )


def _candidate_sources(
    commit: str,
    *,
    history: LineageHistoryEvidence,
    placement: PlacementEvidence,
) -> tuple[SuggestFixupCandidateSource, ...]:
    sources: list[SuggestFixupCandidateSource] = []
    if commit in history.candidates:
        sources.append("lineage-history")
    if commit == placement.barrier:
        sources.append("placement-barrier")
    return tuple(sources)


def _candidate_at(
    candidates: tuple[str, ...],
    index: int,
    *,
    history: LineageHistoryEvidence,
    placement: PlacementEvidence,
) -> SuggestFixupCandidate | None:
    if index < 0 or index >= len(candidates):
        return None
    commit = candidates[index]
    return SuggestFixupCandidate(
        commit=commit,
        iteration=index + 1,
        total=len(candidates),
        sources=_candidate_sources(
            commit,
            history=history,
            placement=placement,
        ),
    )


def last_suggest_fixup_candidate(
    *,
    state: SuggestFixupState | None,
    candidates: tuple[str, ...],
    history: LineageHistoryEvidence,
    placement: PlacementEvidence,
) -> SuggestFixupCandidate | None:
    """Return the persisted candidate when it still belongs to the search."""
    if state is None or "last_shown_commit" not in state:
        return None
    try:
        index = candidates.index(state["last_shown_commit"])
    except ValueError:
        return None
    if state.get("iteration") != index + 1:
        return None
    return _candidate_at(
        candidates,
        index,
        history=history,
        placement=placement,
    )


def advance_suggest_fixup_candidate(
    *,
    state: SuggestFixupState | None,
    target: SuggestFixupSearchTarget,
    candidates: tuple[str, ...],
    history: LineageHistoryEvidence,
    placement: PlacementEvidence,
) -> SuggestFixupCandidate | None:
    """Persist and return the next candidate, or None when exhausted."""
    if state is None or "last_shown_commit" not in state:
        next_index = 0
    else:
        try:
            next_index = candidates.index(state["last_shown_commit"]) + 1
        except ValueError:
            next_index = 0

    candidate = _candidate_at(
        candidates,
        next_index,
        history=history,
        placement=placement,
    )
    if candidate is None:
        return None

    write_suggest_fixup_state(
        {
            **target.persisted_search(),
            "last_shown_commit": candidate.commit,
            "iteration": candidate.iteration,
        }
    )
    return candidate
