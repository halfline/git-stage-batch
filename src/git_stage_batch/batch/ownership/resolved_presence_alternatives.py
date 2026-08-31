"""Find selected ranges separated by a later copy of the same text."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...core.coordinates import BatchSourceSpace, LineBoundary, LineSpan
from ...core.line_selection import LineRanges
from ..line_matching.line_range_view import LineRangeView
from ..line_matching.occurrence_index import normalized_line_payload
from ..line_matching.sequence_equality import line_slice_equals


@dataclass(frozen=True, slots=True)
class ResolvedPresenceSourceAlternative:
    """Two selected ranges with a later copy of the first between them."""

    leading_separator: LineSpan[BatchSourceSpace]
    claimed_prefix: LineSpan[BatchSourceSpace]
    contiguous_suffix: LineSpan[BatchSourceSpace]
    claimed_suffix: LineSpan[BatchSourceSpace]

    def __post_init__(self) -> None:
        if len(self.leading_separator) != 1:
            raise ValueError("presence alternative separator must be one line")
        if self.leading_separator.end != self.claimed_prefix.start:
            raise ValueError("presence alternative separator is not adjacent")
        if self.contiguous_suffix.start != self.claimed_prefix.end:
            raise ValueError("presence alternative completion is not adjacent")
        if len(self.contiguous_suffix) != len(self.claimed_suffix):
            raise ValueError("presence alternative suffix lengths differ")
        if self.contiguous_suffix.end.offset > self.claimed_suffix.start.offset:
            raise ValueError("presence alternative source spans overlap")

    @property
    def claimed_ranges(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Return the two one-based claimed source ranges."""
        return (
            (
                self.claimed_prefix.start.offset + 1,
                self.claimed_prefix.end.offset,
            ),
            (
                self.claimed_suffix.start.offset + 1,
                self.claimed_suffix.end.offset,
            ),
        )

    @property
    def leading_separator_line(self) -> int:
        """Return the one-based blank line before the earlier range."""
        return self.leading_separator.start.offset + 1


def resolve_presence_source_alternatives(
    presence_lines: LineRanges,
    source_lines: Sequence[bytes],
) -> tuple[ResolvedPresenceSourceAlternative, ...]:
    """Find neighboring ranges separated by a copy of the first range.

    Only neighbors are compared. Runtime is linear in the selected text, and
    memory grows with the number of matches rather than the number of lines.
    """
    ranges = presence_lines.ranges()
    if ranges and ranges[-1][1] > len(source_lines):
        raise ValueError("presence alternative exceeds its source snapshot")

    alternatives: list[ResolvedPresenceSourceAlternative] = []
    for range_index in range(len(ranges) - 1):
        prefix_start, prefix_end = ranges[range_index]
        suffix_start, suffix_end = ranges[range_index + 1]
        if prefix_start <= 1:
            continue
        separator_line = prefix_start - 1
        if normalized_line_payload(source_lines[separator_line - 1]):
            continue

        suffix_length = suffix_end - suffix_start + 1
        contiguous_suffix_start = prefix_end + 1
        contiguous_suffix_end = prefix_end + suffix_length
        if contiguous_suffix_end >= suffix_start:
            continue
        claimed_suffix = LineRangeView(
            source_lines,
            suffix_start - 1,
            suffix_end,
        )
        if not line_slice_equals(
            source_lines,
            contiguous_suffix_start - 1,
            claimed_suffix,
        ):
            continue

        alternatives.append(
            ResolvedPresenceSourceAlternative(
                leading_separator=LineSpan(
                    LineBoundary(separator_line - 1),
                    LineBoundary(separator_line),
                ),
                claimed_prefix=LineSpan(
                    LineBoundary(prefix_start - 1),
                    LineBoundary(prefix_end),
                ),
                contiguous_suffix=LineSpan(
                    LineBoundary(contiguous_suffix_start - 1),
                    LineBoundary(contiguous_suffix_end),
                ),
                claimed_suffix=LineSpan(
                    LineBoundary(suffix_start - 1),
                    LineBoundary(suffix_end),
                ),
            )
        )
    return tuple(alternatives)
