"""Remaining hunk estimation for session progress."""

from __future__ import annotations

from .live_change_candidates import iter_eligible_live_changes


def estimate_remaining_hunks() -> int:
    """Estimate the number of live hunks not yet included, skipped, or discarded."""
    return sum(1 for _candidate in iter_eligible_live_changes())
