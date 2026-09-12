"""Prove complete-source replay against a saved baseline."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...core.buffer import LineBuffer
from ...core.mapped_storage import MappedIntVector
from ...core.text_lines import normalize_line_sequence_endings
from ..line_matching.sequence_equality import line_sequences_equal
from ..ownership.model import BatchOwnership
from ..realized_file_content import build_realized_buffer_from_lines
from .baseline_reference_positions import baseline_reference_insertion_position


def _target_only_removed_insertion_separators(
    baseline: Sequence[bytes],
    source: Sequence[bytes],
    target: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Accept only missing blank lines directly bordering saved insertions."""
    presence = ownership.presence_line_set()
    with MappedIntVector(
        len(baseline), width=4, fill=0, spool_dir=spool_dir
    ) as separators:
        for claim in ownership.presence_claims:
            for source_line, reference in claim.baseline_references.items():
                position = baseline_reference_insertion_position(reference, baseline)
                if (
                    position is not None
                    and position > 0
                    and 1 < source_line <= len(source)
                    and source_line - 1 not in presence
                    and not baseline[position - 1].strip()
                    and source[source_line - 2] == baseline[position - 1]
                ):
                    separators[position - 1] = 1
        target_index = 0
        for baseline_index, line in enumerate(baseline):
            if target_index < len(target) and target[target_index] == line:
                target_index += 1
            elif not separators[baseline_index]:
                return False
        return target_index == len(target)


def try_replay_complete_source(
    baseline: Sequence[bytes],
    source: Sequence[bytes],
    target: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer | None:
    """Replay only a complete source whose baseline still explains the target.

    Formatting may remove the unowned blank immediately before an owned
    insertion. Restoring that separator is justified by the recorded insertion
    boundary and the complete source, not by ignoring arbitrary whitespace.
    """
    normalized_baseline = normalize_line_sequence_endings(baseline)
    normalized_source = normalize_line_sequence_endings(source)
    normalized_target = normalize_line_sequence_endings(target)
    if not line_sequences_equal(normalized_baseline, normalized_target) and not (
        _target_only_removed_insertion_separators(
            normalized_baseline,
            normalized_source,
            normalized_target,
            ownership,
            spool_dir=spool_dir,
        )
    ):
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
