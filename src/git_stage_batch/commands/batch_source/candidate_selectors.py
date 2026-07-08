"""Candidate selector helpers for batch-source action commands."""

from __future__ import annotations

from ...batch.source_selector import (
    BatchSourceSelector,
    CandidateOperation,
    parse_batch_source_selector,
    require_candidate_operation,
)
from ...exceptions import exit_with_error
from ...i18n import _


def resolve_batch_source_action_selector(
    raw_selector: str,
    expected_operation: CandidateOperation,
    *,
    file: str | None,
) -> BatchSourceSelector:
    """Parse and validate a batch-source selector for an action command."""
    selector = parse_batch_source_selector(raw_selector)
    require_candidate_operation(
        selector,
        expected_operation,
        raw_value=raw_selector,
        file=file,
    )
    if (
        selector.candidate_operation == expected_operation
        and selector.candidate_ordinal is None
    ):
        exit_with_error(
            _(
                "'{selector}' names the {operation} candidate preview set.\n"
                "Use 'git-stage-batch show --from {selector}' to preview candidates, "
                "or use '{batch}:{operation}:N' to {operation} a candidate."
            ).format(
                selector=raw_selector,
                batch=selector.batch_name,
                operation=expected_operation,
            )
        )
    if selector.candidate_ordinal is not None and file is None:
        exit_with_error(
            _(
                "Candidate selector '{selector}' requires --file in this implementation.\n"
                "No changes applied."
            ).format(selector=raw_selector)
        )
    return selector
