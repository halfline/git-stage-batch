"""Localized labels for batch operation candidates."""

from __future__ import annotations

from .operation_candidate_types import CandidateOperation
from ..i18n import pgettext


def candidate_operation_label(operation: CandidateOperation) -> str:
    """Return a localized prose label without changing CLI selector syntax."""
    if operation == "apply":
        return pgettext("candidate operation noun", "apply")
    if operation == "include":
        return pgettext("candidate operation noun", "include")
    raise ValueError(f"unknown candidate operation: {operation}")
