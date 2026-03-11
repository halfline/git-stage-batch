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
