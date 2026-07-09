"""Detached ownership copies for use outside acquisition scopes."""

from __future__ import annotations

from ..core.buffer import LineBuffer
from .absence_content import copy_absence_content as _copy_absence_content
from .ownership import AbsenceClaim, BatchOwnership, ReplacementUnit
from .ownership_acquisition import AcquiredBatchOwnership
from .ownership_claims import PresenceClaim


def acquire_detached_batch_ownership(
    ownership: BatchOwnership,
) -> AcquiredBatchOwnership[BatchOwnership]:
    """Acquire an ownership copy with independent absence content buffers."""
    buffers: list[LineBuffer] = []
    deletions: list[AbsenceClaim] = []
    try:
        for deletion in ownership.deletions:
            content_lines = _copy_absence_content(deletion.content_lines)
            buffers.append(content_lines)
            deletions.append(
                AbsenceClaim(
                    anchor_line=deletion.anchor_line,
                    content_lines=content_lines,
                    baseline_reference=deletion.baseline_reference,
                )
            )
    except Exception:
        for buffer in buffers:
            buffer.close()
        raise

    return AcquiredBatchOwnership(
        ownership=BatchOwnership(
            presence_claims=[
                PresenceClaim(
                    source_lines=claim.source_lines[:],
                    baseline_references=dict(claim.baseline_references),
                )
                for claim in ownership.presence_claims
            ],
            deletions=deletions,
            replacement_units=[
                ReplacementUnit(
                    presence_lines=unit.presence_lines[:],
                    deletion_indices=unit.deletion_indices[:],
                    origin=unit.origin,
                )
                for unit in ownership.replacement_units
            ],
        ),
        buffers=buffers,
    )
