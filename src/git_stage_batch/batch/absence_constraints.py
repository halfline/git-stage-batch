"""Absence-constraint application for realized batch entries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING, Any

from ..exceptions import (
    MergeError as _MergeError,
    MissingAnchorError as _MissingAnchorError,
)
from ..i18n import _
from ..core.text_lines import normalize_line_endings
from .merge_candidates import MergeResolution as _MergeResolution
from .realized_boundaries import (
    boundary_choices_after_source_line as _boundary_choices_for_source_line,
    find_boundary_after_source_line as _locate_boundary_after_source_line,
    find_realization_fallback_boundary as _realization_fallback_boundary,
)
from .realized_entries import RealizedEntry as _RealizedEntry
from .realized_entry_storage import (
    RealizedEntries,
    as_realized_entries,
    realized_entry_content_at,
    realized_entry_is_claimed_at,
)

if TYPE_CHECKING:
    from .ownership_absence_claims import AbsenceClaim


_DEFAULT_CHOICE_SCAN_CAP = 50


@dataclass(frozen=True)
class AbsenceChoice:
    """Concrete exact-removal choice for one absence claim."""

    choice_index: int
    position: int
    target_after_line: int | None
    target_before_line: int | None
    explanation: str


def apply_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: list[AbsenceClaim],
    *,
    strict: bool = True,
    resolution: _MergeResolution | None = None,
) -> RealizedEntries:
    """Apply absence constraints with boundary enforcement.

    For each absence claim:
    1. Find the structural boundary after the anchor line
    2. Suppress forbidden sequence at that boundary using appropriate mode

    Two enforcement modes controlled by 'strict' parameter:

    Strict mode (strict=True) - for applying batch ownership:
    - Used when merging into live working tree that may have diverged
    - Exact match at boundary: suppress
    - Found nearby but not at boundary: raise MergeError (structural conflict)
    - Not found: no-op (already suppressed or never existed)

    Realization mode (strict=False) - for realized batch content construction:
    - Used when building display/storage content from baseline
    - Exact match at boundary: suppress
    - Not at boundary: no-op (baseline may not have content there)

    Both modes fail if anchor boundary itself cannot be determined (MissingAnchorError
    or AmbiguousAnchorError), as this indicates a real structural inconsistency.

    Args:
        entries: Realized entries with source provenance from presence pass
        deletion_claims: Absence constraints with structural anchors
        strict: If True, use strict enforcement (merge). If False, lenient
            enforcement for realization.

    Returns:
        Entries with forbidden sequences suppressed at their anchored boundaries

    Raises:
        MissingAnchorError: If anchor line is not present in realized content
        AmbiguousAnchorError: If anchor boundary cannot be determined uniquely
        MergeError: If strict=True and sequence is found nearby but not at boundary
    """
    result = as_realized_entries(entries)
    if not deletion_claims:
        return result

    suppress_fn = (
        _suppress_at_boundary_strict
        if strict else
        _suppress_at_boundary_for_realization
    )

    for claim_index, claim in enumerate(deletion_claims):
        if not claim.content_lines:
            continue

        forbidden_sequence = [
            normalize_line_endings(line)
            for line in claim.content_lines
        ]

        ambiguity_key = absence_ambiguity_key(
            claim_index,
            claim.anchor_line,
            forbidden_sequence,
        )

        if resolution is not None and ambiguity_key in resolution.decisions:
            old_result = result
            result = _suppress_absence_with_resolution(
                result,
                claim.anchor_line,
                forbidden_sequence,
                ambiguity_key,
                resolution,
            )
            if result is not old_result and old_result is not entries:
                old_result.close()
            continue

        try:
            boundary = _locate_boundary_after_source_line(result, claim.anchor_line)
        except _MissingAnchorError:
            if strict:
                raise
            boundary = _realization_fallback_boundary(result, claim.anchor_line)

        old_result = result
        result = suppress_fn(result, boundary, forbidden_sequence)
        if result is not old_result and old_result is not entries:
            old_result.close()

    return result


def absence_ambiguity_key(
    claim_index: int,
    anchor_line: int | None,
    forbidden_sequence: Sequence[bytes],
) -> str:
    """Return the merge-resolution key for one absence ambiguity."""
    anchor = "start" if anchor_line is None else str(anchor_line)
    digest = hashlib.sha256(b"".join(forbidden_sequence)).hexdigest()[:12]
    return f"absence:{claim_index}:{anchor}:{digest}"


def absence_choices_for_claim(
    entries: Sequence[_RealizedEntry],
    anchor_line: int | None,
    forbidden_sequence: Sequence[bytes],
    *,
    max_results: int | None = None,
) -> tuple[AbsenceChoice, ...]:
    """Return concrete exact-removal choices for one absence claim."""
    positions: list[tuple[int, str]] = []
    seen: set[int] = set()

    def add_position(position: int, explanation: str) -> None:
        if position in seen:
            return
        if not _sequence_matches_at_position(entries, position, list(forbidden_sequence)):
            return
        seen.add(position)
        positions.append((position, explanation))

    for boundary in _boundary_choices_for_source_line(entries, anchor_line):
        add_position(boundary, _("deletion content appears at the anchored boundary"))
        after_claimed = _position_after_claimed_insertions_at_boundary(
            entries,
            boundary,
        )
        if after_claimed != boundary:
            add_position(
                after_claimed,
                _("deletion content appears after claimed insertions at the anchored boundary"),
            )

    if len(positions) <= 1:
        for position in _iter_sequence_occurrences_nearby(
            entries,
            _boundary_choices_for_source_line(entries, anchor_line)[0],
            forbidden_sequence,
            window=20,
            max_results=(max_results or _DEFAULT_CHOICE_SCAN_CAP) + 1,
        ):
            add_position(position, _("deletion content appears nearby"))

    positions.sort(key=lambda item: item[0])
    if max_results is not None:
        positions = positions[:max_results]

    choices = []
    for index, (position, explanation) in enumerate(positions, start=1):
        after_line = None if position == 0 else position
        before_line = (
            None
            if position + len(forbidden_sequence) >= len(entries)
            else position + 1
        )
        choices.append(
            AbsenceChoice(
                choice_index=index,
                position=position,
                target_after_line=after_line,
                target_before_line=before_line,
                explanation=explanation,
            )
        )
    return tuple(choices)


def _suppress_absence_with_resolution(
    entries: Sequence[_RealizedEntry],
    anchor_line: int | None,
    forbidden_sequence: list[bytes],
    ambiguity_key: str,
    resolution: _MergeResolution,
) -> RealizedEntries:
    choice_index = resolution.decisions.get(ambiguity_key)
    if choice_index is None:
        raise _MergeError(_("Missing merge resolution for {key}").format(key=ambiguity_key))
    choices = absence_choices_for_claim(
        entries,
        anchor_line,
        forbidden_sequence,
        max_results=_DEFAULT_CHOICE_SCAN_CAP + 1,
    )
    for choice in choices:
        if choice.choice_index == choice_index:
            return _remove_sequence_at_position(entries, choice.position, forbidden_sequence)
    raise _MergeError(_("Selected merge resolution is no longer valid"))


def _normalize_line_content(content: Any) -> bytes:
    return normalize_line_endings(bytes(content))


def _sequence_matches_at_position(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: list[bytes],
) -> bool:
    """Check if sequence matches entries starting at exact position."""
    if position + len(sequence) > len(entries):
        return False

    return all(
        _normalize_line_content(
            realized_entry_content_at(entries, position + i)
        ) == sequence[i]
        for i in range(len(sequence))
    )


def _find_sequence_nearby(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: list[bytes],
    window: int = 20,
) -> int | None:
    """Search for sequence within window after position."""
    search_end = min(position + window, len(entries) - len(sequence) + 1)

    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, sequence):
            return check_pos

    return None


def _iter_sequence_occurrences_nearby(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: Sequence[bytes],
    *,
    window: int,
    max_results: int,
) -> Iterator[int]:
    """Yield exact nearby sequence positions after a boundary."""
    search_end = min(position + window, len(entries) - len(sequence) + 1)
    result_count = 0
    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, list(sequence)):
            yield check_pos
            result_count += 1
            if result_count >= max_results:
                return


def _remove_sequence_at_position(
    entries: Sequence[_RealizedEntry],
    position: int,
    sequence: list[bytes],
) -> RealizedEntries:
    """Remove sequence from entries at exact position."""
    return as_realized_entries(entries).without_range(
        position,
        position + len(sequence),
    )


def _position_after_claimed_insertions_at_boundary(
    entries: Sequence[_RealizedEntry],
    position: int,
) -> int:
    """Return the first position after contiguous claimed entries at boundary."""
    check_pos = position

    if isinstance(entries, RealizedEntries):
        for run in entries.provenance_runs(position, len(entries)):
            if not run.is_claimed:
                break
            check_pos = run.dest_end
        return check_pos

    while check_pos < len(entries) and realized_entry_is_claimed_at(
        entries,
        check_pos,
    ):
        check_pos += 1

    return check_pos


def _suppress_at_boundary_strict(
    entries: Sequence[_RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes],
) -> RealizedEntries:
    """Suppress forbidden sequence with strict enforcement for merge operations."""
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position,
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence,
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence,
            )

    nearby_pos = _find_sequence_nearby(entries, position, forbidden_sequence, window=20)
    if nearby_pos is not None:
        raise _MergeError(
            _("Batch was created from a different version of the file")
        )

    return as_realized_entries(entries)


def _suppress_at_boundary_for_realization(
    entries: Sequence[_RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes],
) -> RealizedEntries:
    """Suppress forbidden sequence leniently for content realization."""
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position,
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence,
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence,
            )

    return as_realized_entries(entries)
