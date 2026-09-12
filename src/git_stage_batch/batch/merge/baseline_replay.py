"""Prove complete-source replay against a saved baseline."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...core.buffer import LineBuffer
from ...core.text_lines import normalize_line_sequence_endings
from ..line_matching.sequence_equality import line_sequences_equal
from ..ownership.model import BatchOwnership
from ..realized_file_content import build_realized_buffer_from_lines


def try_replay_complete_source(
    baseline: Sequence[bytes],
    source: Sequence[bytes],
    target: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer | None:
    """Replay a complete source only onto its saved baseline."""
    normalized_baseline = normalize_line_sequence_endings(baseline)
    normalized_source = normalize_line_sequence_endings(source)
    normalized_target = normalize_line_sequence_endings(target)
    if not line_sequences_equal(normalized_baseline, normalized_target):
        return None
    candidate = build_realized_buffer_from_lines(
        baseline,
        source,
        ownership,
        preferred_line_ending_lines=target,
        spool_dir=spool_dir,
    )
    accepted = False
    try:
        accepted = line_sequences_equal(
            normalize_line_sequence_endings(candidate), normalized_source
        )
        return candidate if accepted else None
    finally:
        if not accepted:
            candidate.close()
