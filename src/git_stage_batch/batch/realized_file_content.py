"""Realized text file content built from batch ownership."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from ..core.buffer import LineBuffer
from ..editor.line_endings import (
    detect_line_ending,
    restore_line_endings_in_chunks,
)
from ..core.text_lines import normalize_line_sequence_endings
from .presence_constraints import satisfy_constraints
from .realization.entry_storage import realized_entry_content_chunks

if TYPE_CHECKING:
    from .ownership import BatchOwnership


def build_realized_buffer_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
) -> LineBuffer:
    """Build realized batch content as a line buffer."""
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _stream_realized_content_chunks_from_lines(
                normalize_line_sequence_endings(base_lines),
                normalize_line_sequence_endings(batch_source_lines),
                ownership,
            ),
            detect_line_ending(batch_source_lines),
        )
    )


def _stream_realized_content_chunks_from_lines(
    base_lines: Sequence[bytes],
    batch_source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
) -> Iterator[bytes]:
    """Yield realized batch content chunks from normalized line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    realized_entries = satisfy_constraints(
        batch_source_lines,
        base_lines,
        presence_line_set,
        deletion_claims,
        strict=False,
    )

    try:
        yield from realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()
