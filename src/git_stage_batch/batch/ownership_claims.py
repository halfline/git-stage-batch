"""Ownership claim line-range construction helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.line_selection import LineRanges, LineSelection

if TYPE_CHECKING:
    from .ownership import PresenceClaim
    from .ownership_references import BaselineReference


def parse_ownership_line_ranges(line_ranges: list[str] | list[int]) -> LineRanges:
    """Parse source line range strings into a selection."""
    return LineRanges.from_specs(line_ranges)


def format_ownership_line_set(
    source_lines: LineSelection | Iterable[int],
) -> list[str]:
    """Format a source line selection as normalized range strings."""
    if isinstance(source_lines, LineRanges):
        return source_lines.to_range_strings()
    source_selection = LineRanges.from_lines(source_lines)
    if not source_selection:
        return []
    return source_selection.to_range_strings()


@dataclass
class LineRangeBuilder:
    """Build a normalized line selection from mostly ordered additions."""

    ranges: list[tuple[int, int]] = field(default_factory=list)
    pending_start: int | None = None
    pending_end: int | None = None

    def add_line(self, line_number: int) -> None:
        if self.pending_start is None or self.pending_end is None:
            self.pending_start = line_number
            self.pending_end = line_number
            return

        if self.pending_start <= line_number <= self.pending_end:
            return

        if line_number == self.pending_end + 1:
            self.pending_end = line_number
            return

        self.ranges.append((self.pending_start, self.pending_end))
        self.pending_start = line_number
        self.pending_end = line_number

    def finish(self) -> LineRanges:
        ranges = list(self.ranges)
        if self.pending_start is not None and self.pending_end is not None:
            ranges.append((self.pending_start, self.pending_end))
        return LineRanges.from_ranges(ranges)


def presence_claims_from_source_lines(
    source_lines: LineSelection | Iterable[int],
    baseline_references: dict[int, BaselineReference] | None = None,
) -> list[PresenceClaim]:
    """Build normalized presence claims from a source-line selection."""
    from .ownership import PresenceClaim

    source_selection = (
        source_lines
        if isinstance(source_lines, LineRanges)
        else LineRanges.from_lines(source_lines)
    )
    if not source_selection:
        return []
    references = baseline_references or {}
    return [
        PresenceClaim(
            source_lines=format_ownership_line_set(source_selection),
            baseline_references={
                line: reference
                for line, reference in references.items()
                if line in source_selection
            },
        )
    ]
