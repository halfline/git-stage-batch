"""Candidate preview helpers for batch-source commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...batch.operation_candidates import (
    OperationCandidatePreview,
    load_candidate_preview_state,
)


def candidate_preview_for_ordinal(
    previews: Sequence[OperationCandidatePreview],
    ordinal: int,
) -> OperationCandidatePreview | None:
    """Return the preview for a one-based ordinal, if present."""
    if ordinal < 1 or ordinal > len(previews):
        return None
    return previews[ordinal - 1]


def candidate_preview_state_matches(
    preview: OperationCandidatePreview,
    ordinal: int,
) -> bool:
    """Return whether the stored preview state still matches the preview."""
    state = load_candidate_preview_state(preview)
    return (
        state is not None
        and state.get("ordinal") == ordinal
        and state.get("candidate_id") == preview.candidate_id
        and state.get("target_fingerprints") == preview.target_fingerprints
        and state.get("target_result_fingerprints")
        == preview.target_result_fingerprints
    )


def close_candidate_previews(
    previews: Iterable[OperationCandidatePreview],
) -> None:
    """Close every candidate preview in the collection."""
    for preview in previews:
        preview.close()
