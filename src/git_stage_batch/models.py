"""Data models for representing git diffs and hunks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HunkHeader:
    """Represents the header line of a unified diff hunk."""
    old_start: int
    old_len: int
    new_start: int
    new_len: int


@dataclass
class SingleHunkPatch:
    """Represents a patch for a single file containing exactly one hunk."""
    old_path: str
    new_path: str
    lines: list[str]  # includes ---/+++ and a single @@ hunk body

    def to_patch_text(self) -> str:
        """Convert the patch to unified diff text format."""
        return "\n".join(self.lines).rstrip("\n") + "\n"


@dataclass
class LineEntry:
    """Represents a single line in a hunk with metadata for line-level selection."""
    id: int | None  # Line ID for selection (None for context lines without changes)
    kind: str  # " " (context), "+" (addition), "-" (deletion)
    old_line_number: int | None  # Line number in old file (None for additions)
    new_line_number: int | None  # Line number in new file (None for deletions)
    text: str  # The line content without the leading +/- marker


@dataclass
class CurrentLines:
    """Represents a hunk with line IDs for line-level selection."""
    path: str
    header: HunkHeader
    lines: list[LineEntry]

    def changed_line_ids(self) -> list[int]:
        """Return list of line IDs that have changes (+ or -)."""
        return [line.id for line in self.lines if line.id is not None]

    def maximum_line_id_digit_count(self) -> int:
        """Return the number of digits needed to display the largest line ID."""
        changed_ids = self.changed_line_ids()
        if not changed_ids:
            return 1
        return len(str(max(changed_ids)))
