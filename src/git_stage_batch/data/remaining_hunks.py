"""Remaining hunk estimation for session progress."""

from __future__ import annotations

from .live_change_candidates import iter_eligible_live_changes


def estimate_remaining_hunks() -> int:
    """Estimate the number of live hunks not yet included, skipped, or discarded."""
    count = 0
    for candidate in iter_eligible_live_changes():
        with candidate:
            count += 1
    return count
