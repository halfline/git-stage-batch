"""Replacement-origin placement choices for baseline-coordinate merges."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING, Any

from ..core.text_lines import normalize_line_endings
from .line_matching.sequence_equality import line_slice_equals as _line_slice_matches

if TYPE_CHECKING:
    from .ownership.absence_claims import AbsenceClaim


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
    unit: Any,
    claimed_lines: Sequence[int],
    working_lines: Sequence[bytes],
    *,
    max_results: int | None = None,
) -> tuple[str | None, tuple[ReplacementOriginChoice, ...]]:
    """Return explicit target placements for an origin-tracked replacement."""
    origin = getattr(unit, "origin", None)
    if origin is None or not claim.content_lines:
        return None, ()

    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
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
        if max_results is not None and len(choices) >= max_results:
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
        claimed_lines,
        forbidden_sequence,
    )
    return key, tuple(choices)


def _replacement_origin_ambiguity_key(
    unit_index: int,
    deletion_index: int,
    origin: Any,
    claimed_lines: Sequence[int],
    forbidden_sequence: Sequence[bytes],
) -> str:
    claimed = ",".join(str(line) for line in claimed_lines)
    digest = _sequence_digest(forbidden_sequence)
    return (
        f"replacement-origin:{unit_index}:delete:{deletion_index}:"
        f"claimed:{claimed}:old:{origin.old_start}-{origin.old_end}:"
        f"new:{origin.new_start}-{origin.new_end}:{digest}"
    )


def _sequence_digest(lines: Sequence[bytes]) -> str:
    hasher = hashlib.sha256()
    for line in lines:
        hasher.update(line)
    return hasher.hexdigest()[:12]
