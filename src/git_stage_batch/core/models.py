"""Data models for representing git diffs and hunks."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, cast, overload


FileChangeType = Literal["added", "modified", "deleted"]


@dataclass(frozen=True, slots=True)
class HunkHeader:
    """Represents the header line of a unified diff hunk."""
    old_start: int
    old_len: int
    new_start: int
    new_len: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (
                self.old_start,
                self.old_len,
                self.new_start,
                self.new_len,
            )
        ):
            raise ValueError("hunk coordinates must be integers")
        if (
            self.old_start < 0
            or self.old_len < 0
            or self.new_start < 0
            or self.new_len < 0
        ):
            raise ValueError("hunk coordinates must be non-negative")

    def remaining_body_counts(
        self,
        old_consumed: int = 0,
        new_consumed: int = 0,
    ) -> tuple[int, int]:
        """Return unconsumed old- and new-side lines in this hunk."""
        return self.old_len - old_consumed, self.new_len - new_consumed

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
    lines: Sequence[bytes]  # includes ---/+++ and a single @@ hunk body, with \n terminators

    def path(self) -> str:
        """Return the repository path that identifies this text hunk."""
        return self.new_path if self.new_path != "/dev/null" else self.old_path


@dataclass
class BinaryFileChange:
    """Represents a change to a binary file in the diff.

    Binary files cannot be patched line-by-line, so they are treated as
    atomic units that can only be included, skipped, or discarded as a whole.
    """
    old_path: str
    new_path: str
    change_type: FileChangeType
    content_fingerprint: str | None = None

    def is_new_file(self) -> bool:
        """Check if this is a newly added binary file."""
        return self.change_type == "added"

    def is_deleted_file(self) -> bool:
        """Check if this is a deleted binary file."""
        return self.change_type == "deleted"

    def path(self) -> str:
        """Return the repository path that identifies this binary change."""
        return self.new_path if self.new_path != "/dev/null" else self.old_path


@dataclass(frozen=True)
class RenameChange:
    """Represents an atomic file rename without content ownership."""
    old_path: str
    new_path: str

    def path(self) -> str:
        """Return the destination path for file-scoped follow-up actions."""
        return self.new_path


@dataclass(frozen=True)
class FileModeChange:
    """Atomic executable-bit change for one regular repository file."""

    file_path: str
    old_mode: str
    new_mode: str
    index_path: str | None = None

    def path(self) -> str:
        """Return the repository path affected by this mode change."""
        return self.file_path


@dataclass(frozen=True)
class FileTypeChange(FileModeChange):
    """Atomic transition between regular, symlink, and other file types."""



@dataclass(frozen=True)
class TextFileDeletionChange:
    """Represents an atomic whole-text-file deletion."""
    old_path: str
    new_path: str = "/dev/null"

    def path(self) -> str:
        """Return the repository path that identifies the deleted file."""
        return self.old_path


@dataclass
class GitlinkChange:
    """Represents a change to a gitlink/submodule pointer.

    Gitlinks are tree/index entries with mode 160000 and object type commit.
    They do not have file content in the superproject, so they are treated as
    atomic changes.
    """
    old_path: str
    new_path: str
    old_oid: str | None
    new_oid: str | None
    change_type: FileChangeType

    def path(self) -> str:
        """Return the repository path that identifies this gitlink change."""
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    def is_new_file(self) -> bool:
        """Check if this is a newly added gitlink."""
        return self.change_type == "added"

    def is_deleted_file(self) -> bool:
        """Check if this is a deleted gitlink."""
        return self.change_type == "deleted"


@dataclass(init=False, frozen=True, slots=True)
class LineEntry:
    """Immutable rendered diff row used at presentation boundaries.

    Invariant: bytes are canonical, strings are derived.
    - text_bytes: Exact bytes from the diff (without +/- prefix)
    """
    id: int | None  # Line ID for selection (None for context lines without changes)
    kind: str  # " " (context), "+" (addition), "-" (deletion)
    old_line_number: int | None  # Line number in old file (None for additions)
    new_line_number: int | None  # Line number in new file (None for deletions)
    text_bytes: bytes  # Canonical line content without the leading +/- marker
    source_line: int | None = None
    baseline_reference_after_line: int | None = None
    baseline_reference_after_text_bytes: bytes | None = None
    has_baseline_reference_after: bool = False
    baseline_reference_before_line: int | None = None
    baseline_reference_before_text_bytes: bytes | None = None
    has_baseline_reference_before: bool = False
    has_trailing_newline: bool = True

    def __init__(
        self,
        id: int | None,
        kind: str,
        old_line_number: int | None,
        new_line_number: int | None,
        text_bytes: bytes | None = None,
        text: str | None = None,
        source_line: int | None = None,
        baseline_reference_after_line: int | None = None,
        baseline_reference_after_text_bytes: bytes | None = None,
        has_baseline_reference_after: bool = False,
        baseline_reference_before_line: int | None = None,
        baseline_reference_before_text_bytes: bytes | None = None,
        has_baseline_reference_before: bool = False,
        has_trailing_newline: bool = True,
    ) -> None:
        if text_bytes is None:
            raise TypeError("LineEntry requires text_bytes")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "old_line_number", old_line_number)
        object.__setattr__(self, "new_line_number", new_line_number)
        object.__setattr__(self, "text_bytes", text_bytes)
        object.__setattr__(self, "source_line", source_line)
        object.__setattr__(
            self,
            "baseline_reference_after_line",
            baseline_reference_after_line,
        )
        object.__setattr__(
            self,
            "baseline_reference_after_text_bytes",
            baseline_reference_after_text_bytes,
        )
        object.__setattr__(
            self,
            "has_baseline_reference_after",
            has_baseline_reference_after,
        )
        object.__setattr__(
            self,
            "baseline_reference_before_line",
            baseline_reference_before_line,
        )
        object.__setattr__(
            self,
            "baseline_reference_before_text_bytes",
            baseline_reference_before_text_bytes,
        )
        object.__setattr__(
            self,
            "has_baseline_reference_before",
            has_baseline_reference_before,
        )
        object.__setattr__(self, "has_trailing_newline", has_trailing_newline)
        if not isinstance(text_bytes, bytes):
            raise TypeError("LineEntry text_bytes must be bytes")
        if kind not in {" ", "+", "-"}:
            raise ValueError("LineEntry kind must be context, addition, or deletion")
        if id is not None and (type(id) is not int or id <= 0):
            raise ValueError("LineEntry display ID must be positive")
        # Git uses zero as the coordinate on an empty side of a hunk.  Rendered
        # rows therefore permit that sentinel even though durable source and
        # baseline coordinates remain one-based.
        for field_name, coordinate in (
            ("old line number", old_line_number),
            ("new line number", new_line_number),
        ):
            if coordinate is not None and (
                type(coordinate) is not int or coordinate < 0
            ):
                raise ValueError(f"LineEntry {field_name} must be non-negative")
        for field_name, coordinate in (
            ("source line", source_line),
            ("baseline after line", baseline_reference_after_line),
            ("baseline before line", baseline_reference_before_line),
        ):
            if coordinate is not None and (
                type(coordinate) is not int or coordinate <= 0
            ):
                raise ValueError(f"LineEntry {field_name} must be positive")
        for field_name, content in (
            ("baseline after content", baseline_reference_after_text_bytes),
            ("baseline before content", baseline_reference_before_text_bytes),
        ):
            if content is not None and not isinstance(content, bytes):
                raise TypeError(f"LineEntry {field_name} must be bytes")
        if type(has_baseline_reference_after) is not bool:
            raise TypeError("LineEntry baseline-after flag must be boolean")
        if type(has_baseline_reference_before) is not bool:
            raise TypeError("LineEntry baseline-before flag must be boolean")
        if type(has_trailing_newline) is not bool:
            raise TypeError("LineEntry trailing-newline flag must be boolean")


    def display_text(self) -> str:
        """Return display text decoded from canonical bytes."""
        return self.text_bytes.decode("utf-8", errors="replace")

    def with_source_line(
        self,
        source_line: int | None,
    ) -> LineEntry:
        """Return a compatibility row with one projected source coordinate."""
        return replace(self, source_line=source_line)

    def with_baseline_reference(
        self,
        *,
        after_line: int | None,
        after_content: bytes | None,
        has_after: bool,
        before_line: int | None,
        before_content: bytes | None,
        has_before: bool,
    ) -> LineEntry:
        """Return a compatibility row with explicit baseline boundary evidence."""
        return replace(
            self,
            baseline_reference_after_line=after_line,
            baseline_reference_after_text_bytes=after_content,
            has_baseline_reference_after=has_after,
            baseline_reference_before_line=before_line,
            baseline_reference_before_text_bytes=before_content,
            has_baseline_reference_before=has_before,
        )


class _TrackedLineEntries(Sequence[LineEntry]):
    """List-compatible rows carrying an O(1) mutation revision.

    This wrapper owns the caller's existing list instead of copying its
    per-line references. A rendered-view identity can therefore detect row
    changes without another line-scale Python container.
    """

    __slots__ = ("_lines", "revision")

    def __init__(self, lines: list[LineEntry]) -> None:
        self._lines = lines
        self.revision = 0

    def __len__(self) -> int:
        return len(self._lines)

    def __iter__(self) -> Iterator[LineEntry]:
        return iter(self._lines)

    @overload
    def __getitem__(self, index: int) -> LineEntry: ...

    @overload
    def __getitem__(self, index: slice) -> list[LineEntry]: ...

    def __getitem__(self, index: int | slice) -> LineEntry | list[LineEntry]:
        return self._lines[index]

    @overload
    def __setitem__(self, index: int, value: LineEntry) -> None: ...

    @overload
    def __setitem__(self, index: slice, value: Iterable[LineEntry]) -> None: ...

    def __setitem__(
        self,
        index: int | slice,
        value: LineEntry | Iterable[LineEntry],
    ) -> None:
        if isinstance(index, slice):
            self._lines[index] = cast(Iterable[LineEntry], value)
        else:
            self._lines[index] = cast(LineEntry, value)
        self.revision += 1

    def __delitem__(self, index: int | slice) -> None:
        del self._lines[index]
        self.revision += 1

    def insert(self, index: int, value: LineEntry) -> None:
        self._lines.insert(index, value)
        self.revision += 1

    def append(self, value: LineEntry) -> None:
        self._lines.append(value)
        self.revision += 1

    def extend(self, values: Iterable[LineEntry]) -> None:
        self._lines.extend(values)
        self.revision += 1

    def clear(self) -> None:
        self._lines.clear()
        self.revision += 1

    def pop(self, index: int = -1) -> LineEntry:
        value = self._lines.pop(index)
        self.revision += 1
        return value

    def remove(self, value: LineEntry) -> None:
        self._lines.remove(value)
        self.revision += 1

    def reverse(self) -> None:
        self._lines.reverse()
        self.revision += 1

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _TrackedLineEntries):
            return self._lines == other._lines
        return self._lines == other

    def __repr__(self) -> str:
        return repr(self._lines)


@dataclass(frozen=True, slots=True)
class LineLevelChange:
    """Represents a hunk with line IDs for line-level selection."""
    path: str
    header: HunkHeader
    lines: list[LineEntry]

    def __post_init__(self) -> None:
        if isinstance(self.lines, _TrackedLineEntries):
            return
        # A few renderer/test adapters intentionally provide an immutable row
        # tuple.  Retain it without copying; mutable production lists receive
        # the cheap revision wrapper used by stale-view checks.
        if isinstance(self.lines, tuple):
            return
        if not isinstance(self.lines, list):
            raise TypeError("line-level change rows must be a list or tuple")
        object.__setattr__(
            self,
            "lines",
            cast(list[LineEntry], _TrackedLineEntries(self.lines)),
        )

    @property
    def rendered_rows_revision(self) -> int:
        """Return the cheap invalidation token for rendered-row mutation."""
        return (
            self.lines.revision
            if isinstance(self.lines, _TrackedLineEntries)
            else 0
        )

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
