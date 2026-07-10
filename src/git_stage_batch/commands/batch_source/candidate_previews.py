"""Candidate preview helpers for batch-source commands."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ...batch.operation_candidates import (
    OperationCandidatePreview,
)
from ...batch.operation_candidate_state import load_candidate_preview_state
from ...exceptions import CommandError
from ...i18n import _


def candidate_preview_for_ordinal(
    previews: Sequence[OperationCandidatePreview],
    ordinal: int,
) -> OperationCandidatePreview | None:
    """Return the preview for a one-based ordinal, if present."""
    if ordinal < 1 or ordinal > len(previews):
        return None
    return previews[ordinal - 1]


def require_candidate_preview_for_ordinal(
    previews: Sequence[OperationCandidatePreview],
    ordinal: int,
    *,
    batch_name: str,
    operation: str,
    file_path: str,
) -> OperationCandidatePreview:
    """Return the preview for a one-based ordinal or raise a command error."""
    preview = candidate_preview_for_ordinal(previews, ordinal)
    if preview is not None:
        return preview
    if ordinal < 1:
        raise CommandError(_("Candidate ordinal must be at least 1."))
    raise CommandError(
        _("Batch '{batch}' has {count} {operation} candidates for {file}; candidate {ordinal} does not exist.").format(
            batch=batch_name,
            count=len(previews),
            operation=operation,
            file=file_path,
            ordinal=ordinal,
        )
    )


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


def require_candidate_preview_state(
    preview: OperationCandidatePreview,
    ordinal: int,
    *,
    selector: str,
    file_path: str,
) -> None:
    """Raise a command error when the stored preview state is stale or missing."""
    if candidate_preview_state_matches(preview, ordinal):
        return
    raise CommandError(
        _(
            "Candidate selector '{selector}' has not been previewed for {file}.\n"
            "No changes applied.\n\n"
            "Preview it first with:\n"
            "  git-stage-batch show --from {selector} --file {file}"
        ).format(selector=selector, file=file_path)
    )


def close_candidate_previews(
    previews: Iterable[OperationCandidatePreview],
) -> None:
    """Close every candidate preview in the collection."""
    for preview in previews:
        preview.close()
