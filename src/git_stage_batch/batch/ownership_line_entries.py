"""LineEntry helpers shared by ownership translators."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..core.models import LineEntry
from .ownership import (
    BaselineReference,
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from .ownership_claims import LineRangeBuilder
from .replacement_line_runs import ReplacementLineRun


@dataclass
class ReplacementUnitBuilder:
    deletion_indices: list[int]
    claimed_lines: LineRangeBuilder = field(default_factory=LineRangeBuilder)

    def add_presence_line(self, source_line: int) -> None:
        self.claimed_lines.add_line(source_line)

    def finish(self) -> ReplacementUnit:
        return ReplacementUnit(
            presence_lines=self.claimed_lines.finish().to_range_strings(),
            deletion_indices=self.deletion_indices,
        )


def old_line_content_by_number(hunk_lines: list[LineEntry]) -> dict[int, bytes]:
    return {
        line.old_line_number: line.text_bytes
        for line in hunk_lines
        if line.old_line_number is not None and line.kind in {" ", "-"}
    }


def line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


class LineEntryContentSequence(Sequence[bytes]):
    """Lazy byte-line view over LineEntry content."""

    def __init__(self, lines: Sequence[LineEntry]) -> None:
        self._lines = lines

    def __len__(self) -> int:
        return len(self._lines)

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            return LineEntryContentSequence(self._lines[index])
        return line_entry_content(self._lines[index])


def baseline_reference_for_old_line_range(
    old_start: int,
    old_end: int,
    old_line_content: dict[int, bytes],
) -> BaselineReference:
    after_line = old_start - 1 if old_start > 1 else None
    before_line = old_end + 1
    before_content = old_line_content.get(before_line)
    return BaselineReference(
        after_line=after_line,
        after_content=(
            old_line_content.get(after_line)
            if after_line is not None else
            None
        ),
        has_after_line=True,
        before_line=before_line if before_content is not None else None,
        before_content=before_content,
        has_before_line=before_content is not None,
    )


def replacement_unit_origin_for_line_run(
    replacement_run: ReplacementLineRun,
    old_line_content: dict[int, bytes],
) -> ReplacementUnitOrigin:
    """Build parent replacement context for a file-derived replacement run."""
    return ReplacementUnitOrigin(
        old_start=replacement_run.old_start,
        old_end=replacement_run.old_end,
        new_start=replacement_run.new_start,
        new_end=replacement_run.new_end,
        baseline_reference=baseline_reference_for_old_line_range(
            replacement_run.old_start,
            replacement_run.old_end,
            old_line_content,
        ),
    )
