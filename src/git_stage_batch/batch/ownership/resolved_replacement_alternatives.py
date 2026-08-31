"""Read the saved and live versions recorded for replacements."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ...core.coordinates import BatchSourceSpace, LineBoundary, LineSpan
from ...core.line_selection import LineRanges
from .absence_claims import AbsenceClaim
from .claims import parse_ownership_line_ranges
from .replacement_units import ReplacementUnit


class InvalidReplacementAlternatives(ValueError):
    """The recorded saved and live versions are invalid."""


@dataclass(frozen=True, slots=True)
class ResolvedReplacementAlternative:
    """One saved version and the live version stored after it."""

    unit_index: int
    deletion_index: int
    saved: LineSpan[BatchSourceSpace]
    live_payload: tuple[LineSpan[BatchSourceSpace], ...]
    live_envelope: LineSpan[BatchSourceSpace]
    absence_claim: AbsenceClaim

    def __post_init__(self) -> None:
        if type(self.unit_index) is not int or self.unit_index < 0:
            raise ValueError("replacement alternative has an invalid unit index")
        if type(self.deletion_index) is not int or self.deletion_index < 0:
            raise ValueError("replacement alternative has an invalid deletion index")
        if len(self.saved) == 0:
            raise ValueError("replacement alternative saved span must be non-empty")
        if len(self.live_envelope) == 0:
            raise ValueError("replacement alternative live envelope must be non-empty")
        if self.live_envelope.start != self.saved.end:
            raise ValueError("replacement alternative spans are not adjacent")
        if len(self.live_payload) != 1 or self.live_payload[0] != self.live_envelope:
            raise ValueError("replacement alternative live payload is invalid")
        if not self.absence_claim.source_alternative:
            raise ValueError(
                "replacement alternative claim is not an explicit old side"
            )
        if len(self.absence_claim.content_lines) != len(self.live_envelope):
            raise ValueError(
                "replacement alternative live payload has the wrong length"
            )

    @property
    def saved_lines(self) -> LineRanges:
        """Return the saved version's one-based source lines."""
        return LineRanges.from_ranges(
            ((self.saved.start.offset + 1, self.saved.end.offset),)
        )

    def iter_live_source_offsets(self) -> Iterator[int]:
        """Yield the source offsets that hold this pair's live text."""
        yield from range(
            self.live_envelope.start.offset,
            self.live_envelope.end.offset,
        )


def resolve_replacement_alternatives(
    presence_lines: LineRanges,
    deletion_claims: Sequence[AbsenceClaim],
    replacement_units: Sequence[ReplacementUnit],
) -> tuple[ResolvedReplacementAlternative, ...]:
    """Read each saved/live pair and order the pairs by live position."""
    if not any(claim.source_alternative for claim in deletion_claims):
        return ()
    coupled: dict[int, ResolvedReplacementAlternative] = {}
    for unit_index, unit in enumerate(replacement_units):
        saved_lines = parse_ownership_line_ranges(unit.presence_lines)
        alternative_indices: list[int] = []
        for deletion_index in unit.deletion_indices:
            if (
                type(deletion_index) is not int
                or deletion_index < 0
                or deletion_index >= len(deletion_claims)
            ):
                raise InvalidReplacementAlternatives(
                    "replacement unit has an invalid deletion index"
                )
            if deletion_claims[deletion_index].source_alternative:
                alternative_indices.append(deletion_index)
        if not alternative_indices:
            continue
        if len(alternative_indices) != 1:
            raise InvalidReplacementAlternatives(
                "replacement unit has multiple explicit old sides"
            )
        saved_ranges = saved_lines.ranges()
        if len(saved_ranges) != 1:
            raise InvalidReplacementAlternatives(
                "replacement alternative saved side is not contiguous"
            )
        saved_start, saved_end = saved_ranges[0]
        if not presence_lines.contains_range(saved_start, saved_end):
            raise InvalidReplacementAlternatives(
                "replacement alternative saved side is not owned"
            )
        deletion_index = alternative_indices[0]
        if deletion_index in coupled:
            raise InvalidReplacementAlternatives(
                "replacement alternative is coupled to multiple units"
            )
        claim = deletion_claims[deletion_index]
        if not claim.content_lines:
            raise InvalidReplacementAlternatives(
                "replacement alternative live side is empty"
            )
        saved_span: LineSpan[BatchSourceSpace] = LineSpan(
            LineBoundary(saved_start - 1),
            LineBoundary(saved_end),
        )
        live_span: LineSpan[BatchSourceSpace] = LineSpan(
            saved_span.end,
            LineBoundary(saved_span.end.offset + len(claim.content_lines)),
        )
        coupled[deletion_index] = ResolvedReplacementAlternative(
            unit_index=unit_index,
            deletion_index=deletion_index,
            saved=saved_span,
            live_payload=(live_span,),
            live_envelope=live_span,
            absence_claim=claim,
        )

    expected_count = 0
    for deletion_index, claim in enumerate(deletion_claims):
        if not claim.source_alternative:
            continue
        expected_count += 1
        if deletion_index not in coupled:
            raise InvalidReplacementAlternatives(
                "explicit old side is not coupled to a replacement unit"
            )
    if len(coupled) != expected_count:
        raise InvalidReplacementAlternatives(
            "replacement unit couples an unknown explicit old side"
        )
    resolved = sorted(
        coupled.values(),
        key=lambda alternative: (
            alternative.live_envelope.start.offset,
            alternative.live_envelope.end.offset,
            alternative.deletion_index,
        ),
    )
    previous_end = 0
    for alternative in resolved:
        if alternative.live_envelope.start.offset < previous_end:
            raise InvalidReplacementAlternatives(
                "replacement alternative live payloads overlap"
            )
        previous_end = alternative.live_envelope.end.offset
    return tuple(resolved)
