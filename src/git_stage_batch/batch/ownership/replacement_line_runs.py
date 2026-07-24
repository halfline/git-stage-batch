"""Replacement line-run derivation from old and new file content."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ..line_matching.comparison import (
    SemanticChangeKind,
    stream_semantic_change_runs,
)

@dataclass(frozen=True, slots=True)
class ReplacementLineRun:
    """One file-derived replacement run in old-file and new-file coordinates."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int

    def __post_init__(self) -> None:
        if self.old_start > self.old_end:
            raise ValueError("old range start must be <= end")
        if self.new_start > self.new_end:
            raise ValueError("new range start must be <= end")

    def old_line_numbers(self) -> range:
        """Return old-file line numbers without materializing them."""
        return range(self.old_start, self.old_end + 1)

    def new_line_numbers(self) -> range:
        """Return new-file line numbers without materializing them."""
        return range(self.new_start, self.new_end + 1)


def derive_replacement_line_runs_from_lines(
    *,
    old_file_lines: Sequence[bytes],
    new_file_lines: Sequence[bytes],
) -> list[ReplacementLineRun]:
    """Derive replacement line runs from old/new byte-line sequences."""
    return list(
        stream_replacement_line_runs_from_lines(
            old_file_lines=old_file_lines,
            new_file_lines=new_file_lines,
        )
    )


def stream_replacement_line_runs_from_lines(
    *,
    old_file_lines: Sequence[bytes],
    new_file_lines: Sequence[bytes],
) -> Iterator[ReplacementLineRun]:
    """Yield replacement runs without retaining a file-sized Python list."""
    semantic_runs = stream_semantic_change_runs(
        old_file_lines,
        new_file_lines,
    )
    try:
        for run in semantic_runs:
            if (
                run.kind == SemanticChangeKind.REPLACEMENT
                and run.source_start is not None
                and run.source_end is not None
                and run.target_start is not None
                and run.target_end is not None
            ):
                yield ReplacementLineRun(
                    old_start=run.source_start,
                    old_end=run.source_end,
                    new_start=run.target_start,
                    new_end=run.target_end,
                )
    finally:
        close = getattr(semantic_runs, "close", None)
        if close is not None:
            close()
