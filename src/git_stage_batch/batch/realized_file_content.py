"""Realized text file content built from batch ownership."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.buffer import LineBuffer
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..core.text_lines import normalize_line_sequence_endings
from ..exceptions import MergeError as _MergeError
from .line_matching.match import match_lines as _match_lines
from .merge import baseline_edits as _baseline_edits
from .merge.presence_constraints import satisfy_constraints
from .realization.entry_storage import realized_entry_content_chunks

if TYPE_CHECKING:
    from .ownership.model import BatchOwnership


def build_realized_buffer_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    *,
    preferred_line_ending_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> LineBuffer:
    """Build realized content, optionally preferring a target's line endings."""
    line_ending_sources = (
        (batch_source_lines,)
        if preferred_line_ending_lines is None
        else (preferred_line_ending_lines, batch_source_lines)
    )
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _stream_realized_content_chunks_from_lines(
                normalize_line_sequence_endings(base_lines),
                normalize_line_sequence_endings(batch_source_lines),
                ownership,
                spool_dir=spool_dir,
            ),
            choose_line_ending(*line_ending_sources),
        ),
        spool_dir=spool_dir,
    )


def _stream_realized_content_chunks_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes]:
    """Yield realized batch content chunks from normalized line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    baseline_chunks = _baseline_edits.try_apply_baseline_replacement_units(
        batch_source_lines,
        base_lines,
        ownership,
        presence_line_set,
        deletion_claims,
        trust_baseline_coordinates=True,
        spool_dir=spool_dir,
    )
    if baseline_chunks is not None:
        yield from baseline_chunks
        return

    try:
        with (
            _baseline_edits.acquire_deletion_anchor_pairs_for_target(
                batch_source_lines,
                base_lines,
                deletion_claims,
                trust_baseline_coordinates=True,
                spool_dir=spool_dir,
            ) as anchor_pairs,
            _match_lines(
                batch_source_lines,
                base_lines,
                anchor_pairs=anchor_pairs,
                spool_dir=spool_dir,
            ) as mapping,
        ):
            realized_entries = satisfy_constraints(
                batch_source_lines,
                base_lines,
                presence_line_set,
                deletion_claims,
                strict=False,
                source_to_working_mapping=mapping,
                spool_dir=spool_dir,
            )
    except _MergeError:
        baseline_chunks = _baseline_edits.try_apply_baseline_replacement_units(
            batch_source_lines,
            base_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            trust_baseline_coordinates=True,
            spool_dir=spool_dir,
        )
        if baseline_chunks is None:
            raise
        yield from baseline_chunks
        return

    try:
        yield from realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()
