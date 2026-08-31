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
    """One saved version and the live version stored after it.

    A deletion record points to the live text. Other saved/live pairs may be
    nested there. ``live_envelope`` includes nested pairs, while
    ``live_payload`` includes only this pair's live text.
    """

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
        if not self.live_payload:
            raise ValueError("replacement alternative live payload is empty")
        previous_end = self.live_envelope.start.offset
        payload_line_count = 0
        for payload_span in self.live_payload:
            if len(payload_span) == 0:
                raise ValueError("replacement alternative payload span is empty")
            if (
                payload_span.start.offset < previous_end
                or payload_span.start.offset < self.live_envelope.start.offset
                or payload_span.end.offset > self.live_envelope.end.offset
            ):
                raise ValueError(
                    "replacement alternative payload spans are outside live order"
                )
            previous_end = payload_span.end.offset
            payload_line_count += len(payload_span)
        if self.live_payload[0].start != self.live_envelope.start:
            raise ValueError(
                "replacement alternative payload does not start at its live edge"
            )
        if not self.absence_claim.source_alternative:
            raise ValueError(
                "replacement alternative claim is not an explicit old side"
            )
        if len(self.absence_claim.content_lines) != payload_line_count:
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
        for payload_span in self.live_payload:
            yield from range(payload_span.start.offset, payload_span.end.offset)


@dataclass(frozen=True, slots=True)
class _UnresolvedReplacementAlternative:
    """A saved version whose live text has not yet been found."""

    unit_index: int
    deletion_index: int
    saved: LineSpan[BatchSourceSpace]
    absence_claim: AbsenceClaim


def _current_payload_contains(
    current_start: int,
    current_end: int,
    contained: LineSpan[BatchSourceSpace],
) -> bool:
    """Return whether one saved span is inside the current live text."""
    return (
        current_start <= contained.start.offset and contained.end.offset <= current_end
    )


def _resolve_live_geometry(
    unresolved: _UnresolvedReplacementAlternative,
    resolved_by_live_start: dict[int, ResolvedReplacementAlternative],
) -> ResolvedReplacementAlternative:
    """Find this pair's live lines, excluding any nested pairs."""
    live_start = unresolved.saved.end.offset
    source_offset = live_start
    payload_start = live_start
    remaining_payload_lines = len(unresolved.absence_claim.content_lines)
    payload_spans: list[LineSpan[BatchSourceSpace]] = []

    while True:
        nested = resolved_by_live_start.get(source_offset)
        if nested is not None and _current_payload_contains(
            payload_start,
            source_offset,
            nested.saved,
        ):
            if payload_start < source_offset:
                payload_spans.append(
                    LineSpan(
                        LineBoundary(payload_start),
                        LineBoundary(source_offset),
                    )
                )
            source_offset = nested.live_envelope.end.offset
            payload_start = source_offset
            continue
        if not remaining_payload_lines:
            break
        source_offset += 1
        remaining_payload_lines -= 1

    if payload_start < source_offset:
        payload_spans.append(
            LineSpan(
                LineBoundary(payload_start),
                LineBoundary(source_offset),
            )
        )
    return ResolvedReplacementAlternative(
        unit_index=unresolved.unit_index,
        deletion_index=unresolved.deletion_index,
        saved=unresolved.saved,
        live_payload=tuple(payload_spans),
        live_envelope=LineSpan(
            LineBoundary(live_start),
            LineBoundary(source_offset),
        ),
        absence_claim=unresolved.absence_claim,
    )


def resolve_replacement_alternatives(
    presence_lines: LineRanges,
    deletion_claims: Sequence[AbsenceClaim],
    replacement_units: Sequence[ReplacementUnit],
) -> tuple[ResolvedReplacementAlternative, ...]:
    """Read each saved/live pair and order the pairs by live position."""
    if not any(claim.source_alternative for claim in deletion_claims):
        return ()
    coupled: dict[int, _UnresolvedReplacementAlternative] = {}
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
        coupled[deletion_index] = _UnresolvedReplacementAlternative(
            unit_index=unit_index,
            deletion_index=deletion_index,
            saved=saved_span,
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
    resolved_by_live_start: dict[int, ResolvedReplacementAlternative] = {}
    resolved: list[ResolvedReplacementAlternative] = []
    for unresolved in sorted(
        coupled.values(),
        key=lambda alternative: (
            alternative.saved.end.offset,
            alternative.saved.start.offset,
            alternative.deletion_index,
        ),
        reverse=True,
    ):
        live_start = unresolved.saved.end.offset
        if live_start in resolved_by_live_start:
            raise InvalidReplacementAlternatives(
                "replacement alternatives share one live boundary"
            )
        try:
            alternative = _resolve_live_geometry(
                unresolved,
                resolved_by_live_start,
            )
        except ValueError as error:
            raise InvalidReplacementAlternatives(str(error)) from error
        resolved_by_live_start[live_start] = alternative
        resolved.append(alternative)

    resolved.sort(
        key=lambda alternative: (
            alternative.live_envelope.start.offset,
            alternative.live_envelope.end.offset,
            alternative.deletion_index,
        )
    )
    payload_spans = sorted(
        (
            payload_span.start.offset,
            payload_span.end.offset,
            alternative.deletion_index,
        )
        for alternative in resolved
        for payload_span in alternative.live_payload
    )
    previous_end = 0
    for payload_start, payload_end, _deletion_index in payload_spans:
        if payload_start < previous_end:
            raise InvalidReplacementAlternatives(
                "replacement alternative live payloads overlap"
            )
        previous_end = payload_end
    return tuple(resolved)
