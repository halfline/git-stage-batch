"""Test-only controls for process-global journal state."""

from __future__ import annotations

from git_stage_batch.utils import journal


def reset_journal_state() -> None:
    """Discard cached writers after a test changes journal environment."""
    with journal._WRITERS_LOCK:
        journal._WRITERS = {}
        journal._REPOSITORY_IDS = {}
