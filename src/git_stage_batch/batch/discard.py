"""Discard batch ownership from target line sequences."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from .baseline_correspondence import (
    build_baseline_correspondence as _build_discard_baseline_correspondence,
)
from .discard_reversal import (
    reverse_presence_constraints as _reverse_batch_presence_constraints,
)
from .line_matching.line_mapping import LineMapping
from .line_matching.match import match_lines
from .realized_entries import RealizedEntry as _RealizedEntry
from .realized_entry_storage import (
    RealizedEntries,
    as_realized_entries,
    realized_entry_content_chunks as _realized_entry_content_chunks,
)
from . import realized_mapping as _realized_mapping
from .realized_boundaries import (
    find_boundary_after_source_line as _locate_boundary_after_source_line,
    sequence_present_at_boundary as _boundary_sequence_present,
)
from ..core.buffer import (
    LineBuffer,
    buffer_has_data,
)
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MissingAnchorError as _MissingAnchorError,
)
from ..core.text_lines import (
    AcquirableLineSequence,
    normalize_line_sequence_endings,
)

if TYPE_CHECKING:
    from .ownership import BatchOwnership
    from .ownership_absence_claims import AbsenceClaim


def _discard_result_line_ending_from_lines(
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence discard output."""
    result_line_ending = choose_line_ending(working_lines)
    if result_line_ending is not None:
        return result_line_ending
    if buffer_has_data(baseline_lines):
        return choose_line_ending(baseline_lines)
    return choose_line_ending(source_lines)


def discard_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> LineBuffer:
    """Discard ownership and return a buffer with destination line endings."""
    result_line_ending = _discard_result_line_ending_from_lines(
        working_lines,
        baseline_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    normalized_baseline_lines = normalize_line_sequence_endings(baseline_lines)
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _discard_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                normalized_baseline_lines,
            ),
            result_line_ending,
        ),
    )


def _discard_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    baseline_lines: AcquirableLineSequence[Any],
) -> Iterator[bytes]:
    """Discard ownership from normalized byte-line sequences."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
        baseline_lines.acquire_lines() as acquired_baseline_lines,
    ):
        yield from _discard_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            acquired_baseline_lines,
        )


def _discard_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> Iterator[bytes]:
    """Discard ownership from acquired normalized byte-line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    with match_lines(source_lines, working_lines) as working_to_source:
        correspondence = _build_discard_baseline_correspondence(
            baseline_lines,
            source_lines,
        )

        realized_entries = _build_realized_entries_for_discard(
            source_lines,
            working_lines,
            working_to_source,
        )

    try:
        updated_entries = _reverse_batch_presence_constraints(
            realized_entries,
            presence_line_set,
            correspondence,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        updated_entries = _restore_absence_constraints(
            realized_entries,
            deletion_claims,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        yield from _realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()


def _build_realized_entries_for_discard(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    working_to_source: LineMapping,
) -> RealizedEntries:
    """Build structured entries from working tree with source provenance."""
    result = RealizedEntries()
    _realized_mapping.append_working_range_with_mapping(
        result,
        working_lines,
        working_to_source,
        0,
        len(working_lines),
        set(),
    )

    return result


def _restore_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: list['AbsenceClaim'],
) -> RealizedEntries:
    """Restore absence constraints at anchored source boundaries."""
    result = as_realized_entries(entries)
    if not deletion_claims:
        return result

    for claim in deletion_claims:
        try:
            boundary = _locate_boundary_after_source_line(result, claim.anchor_line)
        except _MissingAnchorError:
            continue
        except _AmbiguousAnchorError:
            raise

        if _boundary_sequence_present(result, boundary, claim.content_lines):
            continue

        with RealizedEntries() as restored_entries:
            restored_entries.append_line_range_from(
                claim.content_lines,
                0,
                len(claim.content_lines),
                source_line_start=None,
                is_claimed=False,
            )

            old_result = result
            result = result.insert_entries(boundary, restored_entries)
        if old_result is not entries:
            old_result.close()

    return result
