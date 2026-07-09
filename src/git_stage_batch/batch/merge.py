"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

from . import baseline_edits as _baseline_edits
from . import presence_constraints as _presence_constraints
from .merge_candidate_enumeration import (
    enumerate_merge_batch_candidates_for_lines as _enumerate_merge_candidates,
)
from .merge_candidates import (
    MergeCandidateSet as _MergeCandidateSet,
    MergeResolution as _MergeResolution,
)
from .merge_validation import (
    check_structural_validity as _check_merge_structural_validity,
)
from .line_mapping import LineMapping
from .match import match_lines
from .realized_entries import (
    realized_entry_content_chunks as _realized_entry_content_chunks,
)
from ..core.line_selection import LineSelection
from ..core.buffer import LineBuffer
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..exceptions import (
    MergeError as _MergeError,
)
from ..i18n import _
from ..utils.text import (
    AcquirableLineSequence,
    normalize_line_sequence_endings,
)

if TYPE_CHECKING:
    from .ownership import BatchOwnership


_MERGE_CANDIDATE_CAP = 50


def _byte_chunks(chunks: Iterable[Any]) -> Iterator[bytes]:
    for chunk in chunks:
        yield bytes(chunk)


def _merge_result_line_ending_from_lines(
    primary_lines: Sequence[bytes],
    fallback_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence merge output."""
    return choose_line_ending(primary_lines, fallback_lines)


def merge_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> LineBuffer:
    """Merge line sequences and return a buffer with destination line endings."""
    result_line_ending = _merge_result_line_ending_from_lines(
        working_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _merge_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                source_to_working_mapping=source_to_working_mapping,
                resolution=resolution,
            ),
            result_line_ending,
        )
    )


def can_merge_batch_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> bool:
    """Return whether a normalized line merge can be applied."""
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    try:
        for _chunk in _merge_batch_line_chunks(
            normalized_source_lines,
            ownership,
            normalized_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        ):
            pass
    except _MergeError:
        return False
    return True


def _merge_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> Iterator[bytes]:
    """Merge normalized byte-line sequences and yield normalized chunks."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
    ):
        yield from _merge_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        )


def _merge_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
    resolution: _MergeResolution | None = None,
) -> Iterator[bytes]:
    """Merge acquired normalized line sequences and yield normalized chunks."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    fallback_chunks = _baseline_edits.try_apply_baseline_replacement_units(
        source_lines,
        working_lines,
        ownership,
        presence_line_set,
        deletion_claims,
        resolution=resolution,
        max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
    )
    if fallback_chunks is not None:
        yield from _byte_chunks(fallback_chunks)
        return

    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(source_lines, working_lines)
        mapping = owned_mapping
    try:
        if _baseline_edits.has_missing_origin_replacement_claims(
            ownership,
            presence_line_set,
            source_lines,
            mapping,
        ):
            raise _MergeError(
                _(
                    "Cannot reliably place split replacement: original replacement "
                    "boundary is not present"
                )
            )

        try:
            _check_merge_structural_validity(
                mapping,
                presence_line_set,
                deletion_claims,
                source_lines,
                working_lines
            )
        except _MergeError:
            if resolution is None:
                raise

        realized_entries = _presence_constraints.satisfy_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            deletion_claims,
            source_to_working_mapping=mapping,
            resolution=resolution,
        )
    except _MergeError:
        fallback_chunks = _baseline_edits.try_apply_baseline_replacement_units(
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            resolution=resolution,
            max_resolution_choices=_MERGE_CANDIDATE_CAP + 1,
        )
        if fallback_chunks is not None:
            yield from _byte_chunks(fallback_chunks)
            return
        raise
    finally:
        if owned_mapping is not None:
            owned_mapping.close()

    try:
        yield from _realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()


def enumerate_merge_batch_candidates_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    max_candidates: int = _MERGE_CANDIDATE_CAP,
) -> _MergeCandidateSet:
    """Enumerate safe merge candidates for an otherwise-refused merge.

    The normal merge path remains ambiguity-intolerant. This helper first
    verifies that the ordinary merge refuses, then enumerates supported
    ambiguity choices one at a time.
    """
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    with (
        normalized_source_lines.acquire_lines() as acquired_source_lines,
        normalized_working_lines.acquire_lines() as acquired_working_lines,
    ):
        try:
            for _chunk in _merge_batch_acquired_line_chunks(
                acquired_source_lines,
                ownership,
                acquired_working_lines,
            ):
                pass
            return _MergeCandidateSet(())
        except _MergeError:
            pass

        def resolution_is_valid(candidate_resolution: _MergeResolution) -> bool:
            try:
                for _chunk in _merge_batch_acquired_line_chunks(
                    acquired_source_lines,
                    ownership,
                    acquired_working_lines,
                    resolution=candidate_resolution,
                ):
                    pass
            except _MergeError:
                return False
            return True

        return _enumerate_merge_candidates(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            resolution_is_valid=resolution_is_valid,
            max_candidates=max_candidates,
        )
