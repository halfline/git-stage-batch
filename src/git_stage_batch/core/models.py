"""Data models for representing git diffs and hunks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class HunkHeader:
    """Represents the header line of a unified diff hunk."""
    old_start: int
    old_len: int
    new_start: int
    new_len: int

    def old_prefix_line_count(self) -> int:
        """Return the number of old-file lines before this hunk applies.

        In insertion-only hunks, unified diff uses old_start as the anchor
        before the inserted lines rather than as the first changed old line.
        """
        if self.old_len == 0:
            return max(self.old_start, 0)
        return max(self.old_start - 1, 0)

    def new_prefix_line_count(self) -> int:
        """Return the number of new-file lines before this hunk applies.

        In deletion-only hunks, unified diff uses new_start as the anchor
        before the deleted lines rather than as the first changed new line.
        """
        if self.new_len == 0:
            return max(self.new_start, 0)
        return max(self.new_start - 1, 0)


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
class BinaryFileChange:
    """Represents a change to a binary file in the diff.

    Binary files cannot be patched line-by-line, so they are treated as
    atomic units that can only be included, skipped, or discarded as a whole.
    """
    old_path: str
    new_path: str
    change_type: Literal["added", "modified", "deleted"]

    def is_new_file(self) -> bool:
        """Check if this is a newly added binary file."""
        return self.change_type == "added"

    def is_deleted_file(self) -> bool:
        """Check if this is a deleted binary file."""
        return self.change_type == "deleted"

    def is_modified_file(self) -> bool:
        """Check if this is a modified binary file."""
        return self.change_type == "modified"


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
    baseline_reference_after_line: int | None = None
    baseline_reference_after_text_bytes: bytes | None = None
    has_baseline_reference_after: bool = False
    baseline_reference_before_line: int | None = None
    baseline_reference_before_text_bytes: bytes | None = None
    has_baseline_reference_before: bool = False
    has_trailing_newline: bool = True


@dataclass
class LineLevelChange:
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


@dataclass(frozen=True)
class ReviewActionGroup:
    """One user-visible file-review selection and the actions it supports."""

    display_ids: tuple[int, ...]
    selection_ids: tuple[int, ...]
    actions: tuple[str, ...]
    reason: str = "simple"


@dataclass
class RenderedBatchDisplay:
    """Rendered batch display with gutter ID translation for selection.

    The LineLevelChange contains lines with original selection IDs from batch
    reconstruction. Gutter IDs are filtered display-local IDs (1, 2, 3...)
    assigned only to individually mergeable lines in the current working tree.

    When user selects `--line 1`, that refers to gutter ID 1, which maps to
    an original selection ID via gutter_to_selection_id.

    Attributes:
        line_changes: What gets shown to the user (contains original selection IDs)
        gutter_to_selection_id: Map from filtered gutter number to selection ID (for ownership selection)
        selection_id_to_gutter: Reverse map from selection ID to filtered gutter number
        actionable_selection_groups: Complete original selection-ID groups that may be acted on from review output
        review_gutter_to_selection_id: Map from review gutter number to selection ID
        review_selection_id_to_gutter: Reverse map for review gutter IDs
        review_action_groups: Action-specific groups for page-aware review state
    """
    line_changes: LineLevelChange
    gutter_to_selection_id: dict[int, int]
    selection_id_to_gutter: dict[int, int]
    actionable_selection_groups: tuple[tuple[int, ...], ...] = ()
    review_gutter_to_selection_id: dict[int, int] = field(default_factory=dict)
    review_selection_id_to_gutter: dict[int, int] = field(default_factory=dict)
    review_action_groups: tuple[ReviewActionGroup, ...] = ()
