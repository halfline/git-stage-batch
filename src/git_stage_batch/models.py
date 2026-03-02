"""Data models for representing git diffs, hunks, and lines."""

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
class LineEntry:
    """Represents a single line within a hunk."""
    id: int | None            # assigned only to changed lines; context lines have id=None
    kind: str                    # " " | "+" | "-"
    old_line_number: int | None
    new_line_number: int | None
    text: str                    # content without leading sign


@dataclass
class CurrentLines:
    """Represents a complete hunk with its metadata and line entries."""
    path: str
    header: HunkHeader
    lines: list[LineEntry]

    def changed_line_ids(self) -> list[int]:
        """Return list of IDs for all changed lines (+ or - lines)."""
        return [line_entry.id for line_entry in self.lines if line_entry.id is not None]  # type: ignore

    def maximum_line_id_digit_count(self) -> int:
        """Return the number of digits in the largest line ID (for alignment)."""
        ids = self.changed_line_ids()
        return len(str(max(ids))) if ids else 1


@dataclass
class SingleHunkPatch:
    """Represents a patch for a single file containing exactly one hunk."""
    old_path: str
    new_path: str
    lines: list[str]  # includes ---/+++ and a single @@ hunk body

    def to_patch_text(self) -> str:
        """Convert the patch to unified diff text format."""
        return "\n".join(self.lines).rstrip("\n") + "\n"
