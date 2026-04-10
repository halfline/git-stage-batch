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
    """Represents a patch for a single file containing exactly one hunk.

    Lines are stored as bytes with their \\n terminators preserved (except
    possibly the last line). This preserves exact file content regardless
    of encoding or line ending style.
    """
    old_path: str
    new_path: str
    lines: list[bytes]  # includes ---/+++ and a single @@ hunk body, with \n terminators

    def to_patch_bytes(self) -> bytes:
        """Convert the patch to unified diff bytes format.

        Lines already include their \\n terminators, so we just join them.
        """
        return b"".join(self.lines)


@dataclass
class LineEntry:
    """Represents a single line in a hunk with metadata for line-level selection.

    Invariant: bytes are canonical, strings are derived.
    - text_bytes: Exact bytes from the diff (without +/- prefix)
    - text: Decoded for display (UTF-8 with errors='replace')
    """
    id: int | None  # Line ID for selection (None for context lines without changes)
    kind: str  # " " (context), "+" (addition), "-" (deletion)
    old_line_number: int | None  # Line number in old file (None for additions)
    new_line_number: int | None  # Line number in new file (None for deletions)
    text_bytes: bytes  # Canonical line content without the leading +/- marker
    text: str  # Derived from text_bytes for display (decoded with errors='replace')
    source_line: int | None = None  # Line position in source reference (e.g., batch source, merge base)


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
