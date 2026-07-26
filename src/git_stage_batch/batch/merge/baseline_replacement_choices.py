"""Replacement-origin placement choices for baseline-coordinate merges."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING

from ...core.text_lines import normalize_line_sequence_endings
from ..line_matching.sequence_equality import line_slice_equals as _line_slice_matches

if TYPE_CHECKING:
    from ...core.line_selection import LineSelection
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.replacement_units import (
        ReplacementUnit,
        ReplacementUnitOrigin,
    )


@dataclass(frozen=True)
class ReplacementOriginChoice:
    """Concrete target placement for an origin-tracked replacement."""

    choice_index: int
    position: int
    target_after_line: int | None
    target_before_line: int | None


def replacement_origin_choices_for_unit(
    claim: AbsenceClaim,
    unit_index: int,
    unit: ReplacementUnit,
    claimed_ranges: LineSelection | Iterable[tuple[int, int]],
    working_lines: Sequence[bytes],
    *,
    max_results: int,
) -> tuple[str | None, tuple[ReplacementOriginChoice, ...]]:
    """Return explicit target placements for an origin-tracked replacement.

    Range-record inputs must be normalized and ordered like ``LineSelection.ranges()``.
    """
    if type(max_results) is not int or max_results < 1:
        raise ValueError("max_results must be positive")

    origin = getattr(unit, "origin", None)
    if origin is None or not claim.content_lines:
        return None, ()

    forbidden_sequence = normalize_line_sequence_endings(
        claim.content_lines
    )
    if not forbidden_sequence:
        return None, ()
    if len(forbidden_sequence) > len(working_lines):
        return None, ()

    choices: list[ReplacementOriginChoice] = []
    for position in range(0, len(working_lines) - len(forbidden_sequence) + 1):
        if not _line_slice_matches(working_lines, position, forbidden_sequence):
            continue
        choices.append(
            ReplacementOriginChoice(
                choice_index=len(choices) + 1,
                position=position,
                target_after_line=None if position == 0 else position,
                target_before_line=(
                    None
                    if position + len(forbidden_sequence) >= len(working_lines)
                    else position + len(forbidden_sequence) + 1
                ),
            )
        )
        if len(choices) >= max_results:
            break

    if not choices:
        return None, ()

    deletion_indices = getattr(unit, "deletion_indices", [])
    if len(deletion_indices) != 1:
        return None, ()

    key = _replacement_origin_ambiguity_key(
        unit_index,
        deletion_indices[0],
        origin,
        _replacement_range_records(claimed_ranges),
        forbidden_sequence,
    )
    return key, tuple(choices)


def _replacement_range_records(
    claimed_ranges: LineSelection | Iterable[tuple[int, int]],
) -> Iterable[tuple[int, int]]:
    """Return range records without materializing a streamed input."""
    ranges = getattr(claimed_ranges, "ranges", None)
    if ranges is not None:
        return ranges()
    return claimed_ranges


def _replacement_origin_ambiguity_key(
    unit_index: int,
    deletion_index: int,
    origin: ReplacementUnitOrigin,
    claimed_ranges: Iterable[tuple[int, int]],
    forbidden_sequence: Sequence[bytes],
) -> str:
    claimed = _range_sequence_identity(claimed_ranges)
    digest = _sequence_digest(forbidden_sequence)
    return (
        f"replacement-origin:{unit_index}:delete:{deletion_index}:"
        f"claimed:{claimed}:old:{origin.old_start}-{origin.old_end}:"
        f"new:{origin.new_start}-{origin.new_end}:{digest}"
    )


def _range_sequence_identity(
    ranges: Iterable[tuple[int, int]],
) -> str:
    hasher = hashlib.sha256()
    first_line: int | None = None
    last_line: int | None = None
    range_count = 0
    for start, end in ranges:
        if first_line is None:
            first_line = start
        last_line = end
        range_count += 1
        hasher.update(str(start).encode("ascii"))
        hasher.update(b":")
        hasher.update(str(end).encode("ascii"))
        hasher.update(b";")

    span = "empty" if first_line is None else f"{first_line}-{last_line}"
    return f"{span}:{range_count}:{hasher.hexdigest()[:12]}"


def _sequence_digest(lines: Sequence[bytes]) -> str:
    hasher = hashlib.sha256()
    for line in lines:
        hasher.update(line)
    return hasher.hexdigest()[:12]
