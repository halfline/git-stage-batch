"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

from array import array
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from .match import LineMapping, match_lines
from ..core.line_selection import parse_line_selection
from ..editor import (
    Editor,
    EditorBuffer,
    choose_line_ending,
    buffer_has_data,
    restore_line_endings_in_chunks,
)
from ..exceptions import MergeError, MissingAnchorError, AmbiguousAnchorError
from ..i18n import _
from ..utils.text import (
    AcquirableLineSequence,
    normalize_line_sequence_endings,
    normalize_line_endings,
)

if TYPE_CHECKING:
    from .ownership import BatchOwnership, DeletionClaim


class RegionKind(Enum):
    """Region kind for baseline restoration correspondence.

    Defines how a source-space region should be restored during discard:
    - EQUAL: Unchanged lines, restored line-by-line
    - INSERT: Source-only (batch added), removed during discard
    - REPLACE_LINE_BY_LINE: Changed region with same size, restored line-by-line
    - REPLACE_BY_HUNK: Changed region with different sizes, restored as whole unit
    """
    EQUAL = auto()
    INSERT = auto()
    REPLACE_LINE_BY_LINE = auto()
    REPLACE_BY_HUNK = auto()


@dataclass(slots=True)
class RealizedEntry:
    """A line view in realized content with structural provenance.

    Tracks where each line came from in batch-source space, enabling
    exact anchored boundary resolution for absence constraints.
    """
    content: Any  # Line content with newline
    source_line: int | None  # Batch-source line number (1-indexed), or None for working-tree extras
    target_line: int | None = None  # Working-tree line number (1-indexed), when known
    is_claimed: bool = False  # True if from a claimed source line (presence constraint)


class _RealizedEntries(Sequence[RealizedEntry]):
    """Compact realized content with parallel provenance storage.

    Indexing returns RealizedEntry views for existing helper contracts. Streaming
    and internal lookups use direct accessors so the result does not retain one
    Python object per output line.
    """

    def __init__(self, entries: Iterable[RealizedEntry] = ()) -> None:
        self._editor = Editor(())
        self._source_lines = array("Q")
        self._target_lines = array("Q")
        self._claimed = bytearray()

        for entry in entries:
            self.append_entry(entry)

    def __len__(self) -> int:
        return len(self._source_lines)

    def __getitem__(self, index: int | slice) -> RealizedEntry | _RealizedEntries:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return self.slice(start, stop)

            result = _RealizedEntries()
            for child_index in range(start, stop, step):
                result.append_from(self, child_index)
            return result

        index = self._normalize_index(index)
        return RealizedEntry(
            content=self._editor[index],
            source_line=self.source_line_at(index),
            target_line=self.target_line_at(index),
            is_claimed=self.is_claimed_at(index),
        )

    def append(
        self,
        content: Any,
        *,
        source_line: int | None = None,
        target_line: int | None = None,
        is_claimed: bool = False,
    ) -> None:
        self._editor.append_line_range((content,), 0, 1)
        self._append_metadata(
            source_line=source_line,
            target_line=target_line,
            is_claimed=is_claimed,
        )

    def append_line_from(
        self,
        lines: Sequence[Any],
        index: int,
        *,
        source_line: int | None = None,
        target_line: int | None = None,
        is_claimed: bool = False,
    ) -> None:
        self._editor.append_line_range(lines, index, index + 1)
        self._append_metadata(
            source_line=source_line,
            target_line=target_line,
            is_claimed=is_claimed,
        )

    def _append_metadata(
        self,
        *,
        source_line: int | None,
        target_line: int | None,
        is_claimed: bool,
    ) -> None:
        self._source_lines.append(source_line or 0)
        self._target_lines.append(target_line or 0)
        self._claimed.append(1 if is_claimed else 0)

    def append_entry(self, entry: RealizedEntry) -> None:
        self.append(
            entry.content,
            source_line=entry.source_line,
            target_line=entry.target_line,
            is_claimed=entry.is_claimed,
        )

    def append_from(
        self,
        entries: Sequence[RealizedEntry],
        index: int,
    ) -> None:
        if isinstance(entries, _RealizedEntries):
            index = entries._normalize_index(index)
            self._editor.append_line_ranges_from_editor(
                entries._editor,
                index,
                index + 1,
            )
            self._source_lines.append(entries._source_lines[index])
            self._target_lines.append(entries._target_lines[index])
            self._claimed.append(entries._claimed[index])
            return

        self.append_entry(entries[index])

    def content_at(self, index: int) -> Any:
        return self._editor[self._normalize_index(index)]

    def source_line_at(self, index: int) -> int | None:
        source_line = self._source_lines[self._normalize_index(index)]
        if source_line == 0:
            return None
        return source_line

    def target_line_at(self, index: int) -> int | None:
        target_line = self._target_lines[self._normalize_index(index)]
        if target_line == 0:
            return None
        return target_line

    def is_claimed_at(self, index: int) -> bool:
        return bool(self._claimed[self._normalize_index(index)])

    def content_chunks(self) -> Iterator[bytes]:
        yield from self._editor.line_chunks()

    def slice(self, start: int, stop: int) -> _RealizedEntries:
        result = _RealizedEntries()
        result._append_range_from(self, start, stop)
        return result

    def without_range(self, start: int, stop: int) -> _RealizedEntries:
        result = _RealizedEntries()
        result._append_range_from(self, 0, start)
        result._append_range_from(self, stop, len(self))
        return result

    def insert_entries(
        self,
        position: int,
        entries: Sequence[RealizedEntry],
    ) -> _RealizedEntries:
        inserted = _as_realized_entries(entries)
        result = _RealizedEntries()
        result._append_range_from(self, 0, position)
        result._append_range_from(inserted, 0, len(inserted))
        result._append_range_from(self, position, len(self))
        return result

    def _append_range_from(
        self,
        entries: Sequence[RealizedEntry],
        start: int,
        stop: int,
    ) -> None:
        if start == stop:
            return

        if isinstance(entries, _RealizedEntries):
            self._editor.append_line_ranges_from_editor(entries._editor, start, stop)
            self._source_lines.extend(entries._source_lines[start:stop])
            self._target_lines.extend(entries._target_lines[start:stop])
            self._claimed.extend(entries._claimed[start:stop])
            return

        for index in range(start, stop):
            self.append_entry(entries[index])

    def _normalize_index(self, index: int) -> int:
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return index


def _as_realized_entries(entries: Sequence[RealizedEntry]) -> _RealizedEntries:
    if isinstance(entries, _RealizedEntries):
        return entries
    return _RealizedEntries(entries)


def _entry_content_at(entries: Sequence[RealizedEntry], index: int) -> Any:
    if isinstance(entries, _RealizedEntries):
        return entries.content_at(index)
    return entries[index].content


def _entry_source_line_at(entries: Sequence[RealizedEntry], index: int) -> int | None:
    if isinstance(entries, _RealizedEntries):
        return entries.source_line_at(index)
    return entries[index].source_line


def _entry_target_line_at(entries: Sequence[RealizedEntry], index: int) -> int | None:
    if isinstance(entries, _RealizedEntries):
        return entries.target_line_at(index)
    return entries[index].target_line


def _entry_is_claimed_at(entries: Sequence[RealizedEntry], index: int) -> bool:
    if isinstance(entries, _RealizedEntries):
        return entries.is_claimed_at(index)
    return entries[index].is_claimed


_BaselineLineEdit = tuple[int, int, list[Any]]


def _byte_chunks(chunks: Iterable[Any]) -> Iterator[bytes]:
    for chunk in chunks:
        yield bytes(chunk)


def _normalize_line_content(content: Any) -> bytes:
    return normalize_line_endings(bytes(content))


class _LineRange(Sequence[bytes]):
    """Indexed view over a contiguous range of lines."""

    def __init__(
        self,
        lines: Sequence[bytes],
        start: int,
        end: int,
    ) -> None:
        if start < 0 or end < start:
            raise ValueError("invalid line range")
        self._lines = lines
        self._start = start
        self._end = end

    def __len__(self) -> int:
        return self._end - self._start

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return _LineRange(
                    self._lines,
                    self._start + start,
                    self._start + stop,
                )
            return tuple(self[child_index] for child_index in range(start, stop, step))

        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._lines[self._start + index]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        return all(
            left_line == right_line
            for left_line, right_line in zip(self, other, strict=True)
        )


class _RealizedEntryContentSequence(Sequence[bytes]):
    """Indexed view over realized entry content."""

    def __init__(self, entries: Sequence[RealizedEntry]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step == 1:
                return _LineRange(self, start, stop)
            return tuple(self[child_index] for child_index in range(start, stop, step))

        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return _entry_content_at(self._entries, index)


def _realized_entry_content_chunks(
    entries: Iterable[RealizedEntry],
) -> Iterator[bytes]:
    """Yield content bytes from realized entries."""
    if isinstance(entries, _RealizedEntries):
        yield from entries.content_chunks()
        return

    for entry in entries:
        yield bytes(entry.content)


def _merge_result_line_ending_from_lines(
    primary_lines: Sequence[bytes],
    fallback_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence merge output."""
    return choose_line_ending(primary_lines, fallback_lines)


def _discard_result_line_ending_from_lines(
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence discard output."""
    result_line_ending = choose_line_ending(working_lines)
    if result_line_ending is not None:
        return result_line_ending
    if buffer_has_data(baseline_lines):
        return choose_line_ending(baseline_lines)
    return choose_line_ending(source_lines)


@dataclass
class BaselineRegion:
    """A source-space region with baseline restoration content.

    Represents one contiguous source-side region and the baseline content
    that should be restored when that region is batch-owned and discarded.

    Region kinds:
    - EQUAL: unchanged lines, restored line-by-line
    - INSERT: source-only (batch added), removed when discarded
    - REPLACE_LINE_BY_LINE: changed region (same size), restored line-by-line
    - REPLACE_BY_HUNK: changed region (different sizes), restored as whole unit
    """
    source_start_line: int          # 1-based inclusive
    source_end_line: int            # 1-based inclusive
    baseline_lines: Sequence[bytes]  # baseline content for restoration
    kind: RegionKind                # Region restoration kind
    region_id: int = 0              # Unique region identifier (assigned during construction)


@dataclass
class BaselineCorrespondence:
    """Restoration correspondence from source lines back to baseline regions."""
    regions: list['BaselineRegion']
    _region_start_lines: array = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._region_start_lines = array(
            "Q",
            (
                region.source_start_line
                for region in self.regions
            )
        )

    def get_region_for_source_line(
        self,
        source_line: int
    ) -> 'BaselineRegion | None':
        region_index = bisect_right(self._region_start_lines, source_line) - 1
        if region_index < 0:
            return None

        region = self.regions[region_index]
        if source_line > region.source_end_line:
            return None
        return region


@dataclass(slots=True)
class _BaselineCorrespondenceScanState:
    """Cursors and pending anchor-run bounds while building correspondence."""

    next_region_id: int = 1
    baseline_cursor: int = 0
    source_cursor: int = 0
    run_base_start: int | None = None
    run_source_start: int | None = None
    run_base_end: int = 0
    run_source_end: int = 0

    @property
    def has_run(self) -> bool:
        return self.run_base_start is not None and self.run_source_start is not None


@dataclass
class ClaimedRunIntervalFacts:
    """Structural facts about one contiguous run of missing claimed lines.

    These facts make the merge-time safety decision explicit instead of hiding the
    reasoning inside a single trailing-gap threshold.
    """
    run_start: int
    run_end: int
    run_length: int
    before_source_line: int | None
    after_source_line: int | None
    before_target_line: int | None
    after_target_line: int | None
    leading_unmapped_source_gap: int
    trailing_unmapped_source_gap: int
    bracketed_on_both_sides: bool
    bracketed_on_one_side_only: bool
    source_interval_span: int | None
    target_interval_span: int | None
    surrounding_source_gap_outside_run: int | None
    target_lines_after_before_anchor: int | None
    has_deletion_at_before_anchor: bool
    deletion_line_count_at_before_anchor: int


def _check_structural_validity(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    deletions: list,  # list[DeletionClaim]
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes]
) -> None:
    """Validate that batch can be safely applied given structural alignment.

    Checks:
    1. File hasn't been completely rewritten (zero alignment)
    2. Missing claimed lines have nearby aligned context
    3. Missing deletion anchors have nearby aligned context
    4. Claimed runs have structurally coherent surrounding context

    Check #4 prevents corruption when applying partial selections.
    If claimed lines come from a source region whose surrounding source structure
    no longer maps coherently into the working tree, inserting those lines may
    preserve incompatible working-tree content that should have been replaced.

    Args:
        line_mapping: Alignment between batch source and working tree
        claimed_lines: Set of claimed batch source line numbers
        deletions: List of DeletionClaim objects
        source_lines: Batch source file lines (bytes)
        target_lines: Working tree file lines (bytes)

    Raises:
        MergeError: If structural requirements aren't met
    """
    present_count = sum(1 for line in range(1, len(source_lines) + 1)
                       if line_mapping.is_source_line_present(line))

    if len(target_lines) == 0:
        return

    if present_count == 0 and len(target_lines) > 0:
        if claimed_lines:
            first_claimed = min(claimed_lines)
            raise MergeError(
                _("Cannot reliably place claimed line {line}: file completely rewritten").format(
                    line=first_claimed
                )
            )

    for claimed_line in claimed_lines:
        if claimed_line < 1 or claimed_line > len(source_lines):
            raise MergeError(
                _("Claimed line {line} is out of range (batch source has {count} lines)").format(
                    line=claimed_line,
                    count=len(source_lines)
                )
            )

        if not line_mapping.is_source_line_present(claimed_line):
            has_context_before = False
            has_context_after = False

            for check_line in range(claimed_line - 1, 0, -1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_before = True
                    break

            for check_line in range(claimed_line + 1, len(source_lines) + 1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_after = True
                    break

            if not has_context_before and not has_context_after:
                raise MergeError(
                    _("Cannot reliably place claimed line {line}: surrounding context lost").format(
                        line=claimed_line
                    )
                )

    for deletion in deletions:
        after_line = deletion.anchor_line

        if after_line is not None:
            if after_line < 1 or after_line > len(source_lines):
                raise MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            if not line_mapping.is_source_line_present(after_line):
                has_context = False
                for check_line in range(max(1, after_line - 3), min(len(source_lines) + 1, after_line + 4)):
                    if check_line != after_line and line_mapping.is_source_line_present(check_line):
                        has_context = True
                        break

                if not has_context and after_line != len(source_lines):
                    raise MergeError(
                        _("Cannot determine deletion position after line {line}: anchor and neighbors missing").format(
                            line=after_line
                        )
                    )

    _check_claimed_region_compatibility(
        line_mapping,
        claimed_lines,
        deletions,
        source_lines,
        target_lines
    )


def _check_claimed_region_compatibility(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    deletions: list,  # list[DeletionClaim]
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes]
) -> None:
    """Check if claimed lines come from source regions with structurally coherent context.

    Prevents corruption from partial selections where claimed lines are inserted
    from a source region whose surrounding context is structurally incompatible
    with the working tree.

    For each contiguous run of missing claimed lines:
    1. Find the nearest mapped source boundary before the run
    2. Find the nearest mapped source boundary after the run
    3. Map those boundaries into target-space
    4. Compare the source interval around the run with the available target interval
    5. Reject if the run is weakly anchored next to source-only structure that
       does not fit the target interval coherently

    This is conservative. It is not trying to prove the placement is globally
    optimal; it is trying to reject cases where the run clearly comes from a
    different structural neighborhood than the working tree currently has.

    Args:
        line_mapping: Alignment between source and working tree
        claimed_lines: Set of claimed source line numbers
        source_lines: Batch source lines
        target_lines: Working tree lines

    Raises:
        MergeError: If claimed lines come from incompatible source region
    """
    sorted_missing = _get_missing_claimed_lines(
        line_mapping,
        claimed_lines,
        source_lines
    )

    if not sorted_missing or len(target_lines) == 0:
        return

    for run_start, run_end in _build_contiguous_runs(sorted_missing):
        facts = _collect_claimed_run_interval_facts(
            run_start,
            run_end,
            line_mapping,
            source_lines,
            target_lines,
            deletions
        )

        if not _is_claimed_run_structurally_coherent(facts):
            raise MergeError(
                _("Batch was created from a different version of the file")
            )


def _get_missing_claimed_lines(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    source_lines: Sequence[bytes]
) -> list[int]:
    """Return claimed source lines that are not present in the working tree."""
    missing_claimed = []

    for line_num in sorted(claimed_lines):
        if 1 <= line_num <= len(source_lines):
            if line_mapping.get_target_line_from_source_line(line_num) is None:
                missing_claimed.append(line_num)

    return missing_claimed


def _build_contiguous_runs(sorted_line_numbers: list[int]) -> list[tuple[int, int]]:
    """Build contiguous inclusive runs from sorted line numbers."""
    if not sorted_line_numbers:
        return []

    runs = []
    run_start = sorted_line_numbers[0]
    run_end = sorted_line_numbers[0]

    for line_num in sorted_line_numbers[1:]:
        if line_num == run_end + 1:
            run_end = line_num
        else:
            runs.append((run_start, run_end))
            run_start = line_num
            run_end = line_num

    runs.append((run_start, run_end))
    return runs


def _find_nearest_mapped_source_line_before(
    line_mapping: LineMapping,
    source_line: int
) -> int | None:
    """Find the nearest mapped source line strictly before the given line."""
    for check_line in range(source_line - 1, 0, -1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _find_nearest_mapped_source_line_after(
    line_mapping: LineMapping,
    source_line: int,
    max_source_line: int
) -> int | None:
    """Find the nearest mapped source line strictly after the given line."""
    for check_line in range(source_line + 1, max_source_line + 1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _collect_claimed_run_interval_facts(
    run_start: int,
    run_end: int,
    line_mapping: LineMapping,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    deletions: list
) -> ClaimedRunIntervalFacts:
    """Collect explicit structural facts about one missing claimed run."""
    before_source_line = _find_nearest_mapped_source_line_before(
        line_mapping,
        run_start
    )
    after_source_line = _find_nearest_mapped_source_line_after(
        line_mapping,
        run_end,
        len(source_lines)
    )

    before_target_line = None
    after_target_line = None

    if before_source_line is not None:
        before_target_line = line_mapping.get_target_line_from_source_line(before_source_line)

    if after_source_line is not None:
        after_target_line = line_mapping.get_target_line_from_source_line(after_source_line)

    leading_unmapped_source_gap = 0
    if before_source_line is not None:
        leading_unmapped_source_gap = run_start - before_source_line - 1

    trailing_unmapped_source_gap = 0
    if after_source_line is not None:
        trailing_unmapped_source_gap = after_source_line - run_end - 1
    else:
        trailing_unmapped_source_gap = len(source_lines) - run_end

    bracketed_on_both_sides = (
        before_source_line is not None and
        after_source_line is not None and
        before_target_line is not None and
        after_target_line is not None
    )
    bracketed_on_one_side_only = (
        (before_source_line is None) != (after_source_line is None)
    )

    source_interval_span = None
    target_interval_span = None
    surrounding_source_gap_outside_run = None
    target_lines_after_before_anchor = None
    has_deletion_at_before_anchor = False
    deletion_line_count_at_before_anchor = 0

    if bracketed_on_both_sides:
        source_interval_span = after_source_line - before_source_line - 1
        target_interval_span = after_target_line - before_target_line - 1
        surrounding_source_gap_outside_run = source_interval_span - (run_end - run_start + 1)
    elif before_target_line is not None and after_target_line is None:
        target_lines_after_before_anchor = len(target_lines) - before_target_line

    if before_source_line is not None:
        deletion_line_count_at_before_anchor = sum(
            len(deletion.content_lines)
            for deletion in deletions
            if deletion.anchor_line == before_source_line
        )
        has_deletion_at_before_anchor = deletion_line_count_at_before_anchor > 0

    return ClaimedRunIntervalFacts(
        run_start=run_start,
        run_end=run_end,
        run_length=run_end - run_start + 1,
        before_source_line=before_source_line,
        after_source_line=after_source_line,
        before_target_line=before_target_line,
        after_target_line=after_target_line,
        leading_unmapped_source_gap=leading_unmapped_source_gap,
        trailing_unmapped_source_gap=trailing_unmapped_source_gap,
        bracketed_on_both_sides=bracketed_on_both_sides,
        bracketed_on_one_side_only=bracketed_on_one_side_only,
        source_interval_span=source_interval_span,
        target_interval_span=target_interval_span,
        surrounding_source_gap_outside_run=surrounding_source_gap_outside_run,
        target_lines_after_before_anchor=target_lines_after_before_anchor,
        has_deletion_at_before_anchor=has_deletion_at_before_anchor,
        deletion_line_count_at_before_anchor=deletion_line_count_at_before_anchor,
    )


def _is_claimed_run_structurally_coherent(
    facts: ClaimedRunIntervalFacts
) -> bool:
    """Check if a missing claimed run sits in a coherent source/target interval.

    This does not try to prove the merge is globally correct. It makes a
    conservative local decision from explicit interval facts.

    Unsafe patterns:
    - No mapped anchors at all
    - Both-side anchors exist but are inverted in target-space
    - A large trailing source-only gap sits immediately after the run and the
      before/after target interval is too small to plausibly absorb the
      surrounding source structure
    - The run is anchored only on one side and a large source-only gap extends
      away from the run on the unanchored side
    """
    significant_trailing_gap = facts.trailing_unmapped_source_gap >= 3
    significant_leading_gap = facts.leading_unmapped_source_gap >= 3

    if not facts.bracketed_on_both_sides and not facts.bracketed_on_one_side_only:
        return False

    if facts.bracketed_on_both_sides:
        if facts.before_target_line is None or facts.after_target_line is None:
            return False

        if facts.before_target_line >= facts.after_target_line:
            return False

        if significant_trailing_gap:
            if facts.target_interval_span is None or facts.surrounding_source_gap_outside_run is None:
                return False

            # There is substantial source-side structure after the run before the
            # next reliable source anchor, but almost no room for it in target-space.
            # This is the characteristic shape of the corruption case: the selected
            # run came from a neighborhood with extra source-only structure, so
            # inserting it would preserve incompatible target content nearby.
            if facts.target_interval_span < facts.surrounding_source_gap_outside_run:
                return False

            # Even if the overall interval is not smaller, a run followed by a large
            # source-only tail with little or no target interval is still too weakly
            # bracketed to trust.
            if facts.target_interval_span <= facts.run_length:
                return False

        return True

    # Exactly one-sided anchoring. Be stricter because placement depends on only
    # one reliable boundary.
    if facts.before_source_line is not None and facts.after_source_line is None:
        if significant_trailing_gap:
            if facts.target_lines_after_before_anchor is None:
                return False

            # A source-only tail after the selected run is safe when applying
            # into an empty target tail: this is the append/interleave case
            # that lets independent batches compose in either order.
            if facts.target_lines_after_before_anchor == 0:
                return True

            # A replacement can also be safe with target content after the
            # anchor when an absence constraint at that same boundary removes
            # the whole target tail before the new claimed lines are inserted.
            if (
                facts.has_deletion_at_before_anchor and
                facts.target_lines_after_before_anchor <= facts.deletion_line_count_at_before_anchor
            ):
                return True

            return False
        return True

    if facts.before_source_line is None and facts.after_source_line is not None:
        if significant_leading_gap:
            return False
        return True

    return False


def _apply_presence_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: set[int],
    *,
    source_to_working_mapping: LineMapping | None = None,
) -> _RealizedEntries:
    """Apply presence constraints: ensure all claimed lines exist in result.

    Uses structural alignment to determine which claimed lines are already present
    and adds missing ones at appropriate positions. Returns structured entries
    that preserve batch-source provenance for anchored absence constraints.

    Args:
        source_lines: Batch source file lines (bytes with newlines)
        working_lines: Working tree file lines (bytes with newlines)
        presence_line_set: Set of source line numbers that must be present

    Returns:
        Realized entries with all claimed lines present and provenance preserved
    """
    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(source_lines, working_lines)
        mapping = owned_mapping

    try:
        return _apply_presence_constraints_with_mapping(
            source_lines,
            working_lines,
            presence_line_set,
            mapping,
        )
    finally:
        if owned_mapping is not None:
            owned_mapping.close()


def _apply_presence_constraints_with_mapping(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: set[int],
    mapping: LineMapping,
) -> _RealizedEntries:
    """Apply presence constraints using an existing source-to-working mapping."""

    if not presence_line_set:
        result = _RealizedEntries()
        for working_idx in range(len(working_lines)):
            source_line = mapping.get_source_line_from_target_line(working_idx + 1)
            result.append_line_from(
                working_lines,
                working_idx,
                source_line=source_line,
                target_line=working_idx + 1,
                is_claimed=False,
            )
        return result

    present_claimed: dict[int, int] = {}
    missing_claimed: set[int] = set()

    for source_line in presence_line_set:
        if 1 <= source_line <= len(source_lines):
            working_line = mapping.get_target_line_from_source_line(source_line)
            if working_line is not None:
                present_claimed[source_line] = working_line
            else:
                missing_claimed.add(source_line)

    if not missing_claimed:
        result = _RealizedEntries()
        for working_idx in range(len(working_lines)):
            source_line = mapping.get_source_line_from_target_line(working_idx + 1)
            is_claimed = source_line in presence_line_set if source_line else False
            result.append_line_from(
                working_lines,
                working_idx,
                source_line=source_line,
                target_line=working_idx + 1,
                is_claimed=is_claimed,
            )
        return result

    result = _RealizedEntries()
    working_idx = 0

    for source_line in range(1, len(source_lines) + 1):
        working_line = mapping.get_target_line_from_source_line(source_line)

        if working_line is not None:
            while working_idx < working_line - 1:
                result.append_line_from(
                    working_lines,
                    working_idx,
                    source_line=None,
                    target_line=working_idx + 1,
                    is_claimed=False
                )
                working_idx += 1

            is_claimed = source_line in presence_line_set
            if is_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=True
                )
            else:
                result.append_line_from(
                    working_lines,
                    working_idx,
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=False
                )
            working_idx += 1
        else:
            if source_line in missing_claimed:
                result.append_line_from(
                    source_lines,
                    source_line - 1,
                    source_line=source_line,
                    is_claimed=True
                )

    while working_idx < len(working_lines):
        result.append_line_from(
            working_lines,
            working_idx,
            source_line=None,
            target_line=working_idx + 1,
            is_claimed=False
        )
        working_idx += 1

    return result


def _apply_absence_constraints(
    entries: Sequence[RealizedEntry],
    deletion_claims: list['DeletionClaim'],
    *,
    strict: bool = True
) -> _RealizedEntries:
    """Apply absence constraints with boundary enforcement.

    For each deletion claim:
    1. Find the structural boundary after the anchor line
    2. Suppress forbidden sequence at that boundary using appropriate mode

    Two enforcement modes controlled by 'strict' parameter:

    Strict mode (strict=True) - for applying batch ownership:
    - Used when merging into live working tree that may have diverged
    - Exact match at boundary: suppress
    - Found nearby but not at boundary: raise MergeError (structural conflict)
    - Not found: no-op (already suppressed or never existed)

    Realization mode (strict=False) - for realized batch content construction:
    - Used when building display/storage content from baseline
    - Exact match at boundary: suppress
    - Not at boundary: no-op (baseline may not have content there)

    Both modes fail if anchor boundary itself cannot be determined (MissingAnchorError
    or AmbiguousAnchorError), as this indicates a real structural inconsistency.

    Args:
        entries: Realized entries with source provenance from presence pass
        deletion_claims: Absence constraints with structural anchors
        strict: If True, use strict enforcement (merge). If False, lenient (realization)

    Returns:
        Entries with forbidden sequences suppressed at their anchored boundaries

    Raises:
        MissingAnchorError: If anchor line not present in realized content
        AmbiguousAnchorError: If anchor boundary cannot be determined uniquely
        MergeError: If strict=True and sequence found nearby but not at boundary
    """
    result = _as_realized_entries(entries)
    if not deletion_claims:
        return result

    suppress_fn = _suppress_at_boundary_strict if strict else _suppress_at_boundary_for_realization

    for claim in deletion_claims:
        if not claim.content_lines:
            continue

        # Find boundary (fails if ambiguous or missing - appropriate for both modes)
        try:
            boundary = _find_boundary_after_source_line(result, claim.anchor_line)
        except MissingAnchorError:
            if strict:
                raise
            boundary = _find_realization_fallback_boundary(result, claim.anchor_line)

        # Normalize deletion content for comparison
        forbidden_sequence = [
            normalize_line_endings(line)
            for line in claim.content_lines
        ]

        result = suppress_fn(result, boundary, forbidden_sequence)

    return result


def _missing_claimed_lines(
    entries: Sequence[RealizedEntry],
    presence_line_set: set[int]
) -> set[int]:
    """Return claimed source lines that are not present as claimed entries."""
    present_claimed = set()
    for index in range(len(entries)):
        source_line = _entry_source_line_at(entries, index)
        if source_line is not None and _entry_is_claimed_at(entries, index):
            present_claimed.add(source_line)
    return presence_line_set - present_claimed


def _satisfy_constraints(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_line_set: set[int],
    deletion_claims: list['DeletionClaim'],
    *,
    strict: bool = True,
    source_to_working_mapping: LineMapping | None = None,
) -> _RealizedEntries:
    """Apply presence and absence constraints until claimed lines survive."""
    realized_entries = _apply_presence_constraints(
        source_lines,
        working_lines,
        presence_line_set,
        source_to_working_mapping=source_to_working_mapping,
    )

    realized_entries = _apply_absence_constraints(
        realized_entries,
        deletion_claims,
        strict=strict
    )

    if not _missing_claimed_lines(realized_entries, presence_line_set):
        return realized_entries

    current_lines = _RealizedEntryContentSequence(realized_entries)
    realized_entries = _apply_presence_constraints(
        source_lines,
        current_lines,
        presence_line_set
    )

    realized_entries = _apply_absence_constraints(
        realized_entries,
        deletion_claims,
        strict=strict
    )

    missing_claimed = _missing_claimed_lines(realized_entries, presence_line_set)
    if missing_claimed:
        if not strict:
            return realized_entries
        first_missing = min(missing_claimed)
        raise MergeError(
            _("Cannot satisfy claimed line {line}: removed by absence constraints").format(
                line=first_missing
            )
        )

    return realized_entries


def _find_realization_fallback_boundary(
    entries: Sequence[RealizedEntry],
    source_line: int | None
) -> int:
    """Find a lenient boundary for realization when an anchor is absent.

    Realized batch content may intentionally omit unclaimed source-only lines,
    and earlier absence constraints may remove entries that carried later anchor
    provenance. In that storage/display path, fall back to the nearest earlier
    realized source line and let exact sequence matching decide whether anything
    should be suppressed.
    """
    if source_line is None:
        return 0

    prior_source_lines = []
    for index in range(len(entries)):
        entry_source_line = _entry_source_line_at(entries, index)
        if entry_source_line is not None and entry_source_line < source_line:
            prior_source_lines.append(entry_source_line)
    if not prior_source_lines:
        return 0

    return _find_boundary_after_source_line(entries, max(prior_source_lines))


def _find_boundary_after_source_line(
    entries: Sequence[RealizedEntry],
    source_line: int | None
) -> int:
    """Find the index representing the boundary after a source line.

    The boundary is the position where content anchored "after source line N"
    would appear in the realized output.

    This is strict about ambiguity: if multiple distinct occurrences of the
    same source line exist (e.g., from duplicates or working tree extras),
    we verify there is exactly one claimed occurrence to use as the anchor.

    Args:
        entries: Realized entries with source provenance
        source_line: Anchor line (1-indexed), or None for start-of-file

    Returns:
        Index in entries representing the boundary (0 = start of file)

    Raises:
        MissingAnchorError: If anchor line not present in realized content
        AmbiguousAnchorError: If boundary cannot be determined uniquely
    """
    if source_line is None:
        return 0

    matching_indices = []
    claimed_indices = []

    for i in range(len(entries)):
        if _entry_source_line_at(entries, i) == source_line:
            matching_indices.append(i)
            if _entry_is_claimed_at(entries, i):
                claimed_indices.append(i)

    if not matching_indices:
        raise MissingAnchorError(
            _("Cannot locate anchor boundary after source line {line}: "
              "anchor not present in realized content").format(line=source_line)
        )

    if len(matching_indices) > 1:
        if len(claimed_indices) == 1:
            return claimed_indices[0] + 1
        elif len(claimed_indices) == 0:
            raise AmbiguousAnchorError(
                _("Anchor ambiguity: source line {line} appears {count} times "
                  "in realized content but none are claimed").format(
                    line=source_line, count=len(matching_indices))
            )
        else:
            raise AmbiguousAnchorError(
                _("Anchor ambiguity: source line {line} claimed {count} times").format(
                    line=source_line, count=len(claimed_indices))
            )

    return matching_indices[0] + 1


def _sequence_matches_at_position(
    entries: Sequence[RealizedEntry],
    position: int,
    sequence: list[bytes]
) -> bool:
    """Check if sequence matches entries starting at exact position.

    Args:
        entries: Realized entries
        position: Starting position to check (0-indexed)
        sequence: Normalized sequence to match

    Returns:
        True if sequence matches at position, False otherwise
    """
    if position + len(sequence) > len(entries):
        return False

    return all(
        _normalize_line_content(_entry_content_at(entries, position + i)) == sequence[i]
        for i in range(len(sequence))
    )


def _find_sequence_nearby(
    entries: Sequence[RealizedEntry],
    position: int,
    sequence: list[bytes],
    window: int = 20
) -> int | None:
    """Search for sequence within window after position.

    Args:
        entries: Realized entries
        position: Starting position for search window (0-indexed)
        sequence: Normalized sequence to find
        window: Number of positions to search after position

    Returns:
        Position where sequence was found, or None if not found
    """
    search_end = min(position + window, len(entries) - len(sequence) + 1)

    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, sequence):
            return check_pos

    return None


def _remove_sequence_at_position(
    entries: Sequence[RealizedEntry],
    position: int,
    sequence: list[bytes]
) -> _RealizedEntries:
    """Remove sequence from entries at exact position.

    Args:
        entries: Realized entries
        position: Position where sequence starts (0-indexed)
        sequence: Sequence to remove (length determines how many entries removed)

    Returns:
        New list with sequence removed
    """
    return _as_realized_entries(entries).without_range(
        position,
        position + len(sequence),
    )


def _position_after_claimed_insertions_at_boundary(
    entries: Sequence[RealizedEntry],
    position: int
) -> int:
    """Return the first position after contiguous claimed entries at boundary."""
    check_pos = position

    while check_pos < len(entries) and _entry_is_claimed_at(entries, check_pos):
        check_pos += 1

    return check_pos


def _suppress_at_boundary_strict(
    entries: Sequence[RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes]
) -> _RealizedEntries:
    """Suppress forbidden sequence with strict enforcement for merge operations.

    This enforces absence constraints with two-phase checking:

    Phase 1: Exact boundary enforcement
    - If sequence matches at exact boundary: suppress it (remove from entries)
    - If sequence not at exact boundary: move to phase 2

    Phase 2: Conservative nearby ambiguity check
    - Search within a limited window after the boundary (20 entries)
    - If forbidden sequence appears nearby but not at exact boundary,
      this indicates structural displacement (e.g., presence constraint
      insertions pushed the deletion target away from its anchored position)
    - Raise MergeError rather than silently failing to delete displaced content

    This is not general fuzzy matching. It is a conservative structural safety
    check: the deletion content must be suppressed at the exact anchored boundary.
    Finding it nearby indicates the batch was created from a different file version
    where the deletion target was positioned differently.

    Used when applying batch ownership to live working tree lines.

    Args:
        entries: Realized entries
        position: Exact boundary position to check (0-indexed)
        forbidden_sequence: Sequence that must not appear at this position (normalized)

    Returns:
        Entries with sequence removed if found at exact position

    Raises:
        MergeError: If sequence appears nearby but not at exact boundary (displacement)
    """
    # Phase 1: Check exact match at boundary
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence
            )

    # Phase 2: Check for nearby displacement (conservative safety check)
    nearby_pos = _find_sequence_nearby(entries, position, forbidden_sequence, window=20)
    if nearby_pos is not None:
        raise MergeError(
            _("Batch was created from a different version of the file")
        )

    # Not found - already suppressed or never existed
    return _as_realized_entries(entries)


def _suppress_at_boundary_for_realization(
    entries: Sequence[RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes]
) -> _RealizedEntries:
    """Suppress forbidden sequence with lenient enforcement for content realization.

    This enforces absence constraints only when exact match exists at boundary:
    - If sequence matches at exact boundary: suppress it (remove from entries)
    - If sequence not at exact boundary: no-op (baseline may not have content there)

    Used when building display/storage content from baseline. The baseline may
    legitimately not have the deletion content at the expected anchor, or may
    not have it at all. We only suppress if there's an exact structural match.

    Args:
        entries: Realized entries
        position: Exact boundary position to check (0-indexed)
        forbidden_sequence: Sequence that must not appear at this position (normalized)

    Returns:
        Entries with sequence removed if found at exact position, otherwise unchanged
    """
    # Only suppress if exact match at boundary
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence
            )

    # Not at boundary - no-op, don't suppress
    # (Baseline might not have this content or it might be elsewhere)
    return _as_realized_entries(entries)


def _line_payload_for_reference_match(content: Any) -> bytes:
    """Normalize one line for insertion-boundary identity checks."""
    normalized = _normalize_line_content(content)
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


def _reference_line_matches(
    target_line: bytes,
    reference_content: bytes | None,
) -> bool:
    if reference_content is None:
        return False
    return (
        _line_payload_for_reference_match(target_line) ==
        _line_payload_for_reference_match(reference_content)
    )


def _baseline_reference_insertion_position(
    reference,
    working_lines: Sequence[bytes],
) -> int | None:
    """Return the proven insertion position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position > len(working_lines):
        return None

    verified_boundary = False
    if after_line is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            getattr(reference, "after_content", None),
        ):
            return None
        verified_boundary = True

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        if before_line is None:
            if position != len(working_lines):
                return None
            verified_boundary = True
        else:
            if position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[position],
                getattr(reference, "before_content", None),
            ):
                return None
            verified_boundary = True

    if not verified_boundary:
        return None
    return position


def _baseline_reference_absence_position(
    reference,
    working_lines: Sequence[bytes],
    sequence_length: int,
) -> int | None:
    """Return the proven removal position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position + sequence_length > len(working_lines):
        return None

    after_content = getattr(reference, "after_content", None)
    if after_line is not None and after_content is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            after_content,
        ):
            return None

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        before_position = position + sequence_length
        if before_line is None:
            if before_position != len(working_lines):
                return None
        else:
            if before_position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[before_position],
                getattr(reference, "before_content", None),
            ):
                return None

    return position


def _line_sequences_equal(
    left: Sequence[bytes],
    right: Sequence[bytes],
) -> bool:
    """Return whether two line sequences contain the same bytes."""
    return len(left) == len(right) and all(
        left[index] == right[index]
        for index in range(len(left))
    )


def _line_slice_equals(
    lines: Sequence[bytes],
    start: int,
    expected: Sequence[bytes],
) -> bool:
    """Return whether a sequence slice equals the expected byte lines."""
    if start < 0 or start + len(expected) > len(lines):
        return False
    return all(
        lines[start + offset] == expected[offset]
        for offset in range(len(expected))
    )


def _try_apply_baseline_absence_constraints(
    working_lines: Sequence[bytes],
    deletion_claims: list['DeletionClaim'],
) -> Iterator[bytes] | None:
    """Apply absence-only constraints by exact baseline coordinates."""
    if not deletion_claims:
        return None

    edits: list[_BaselineLineEdit] = []
    for claim in deletion_claims:
        if not claim.content_lines:
            continue
        forbidden_sequence = [
            normalize_line_endings(line)
            for line in claim.content_lines
        ]
        position = _baseline_reference_absence_position(
            claim.baseline_reference,
            working_lines,
            len(forbidden_sequence),
        )
        if position is None:
            return None
        if not _line_slice_equals(working_lines, position, forbidden_sequence):
            return None
        edits.append((position, position + len(forbidden_sequence), []))

    return _apply_non_overlapping_baseline_edits(working_lines, edits)


def _baseline_removal_edit(
    claim: 'DeletionClaim',
    working_lines: Sequence[bytes],
) -> _BaselineLineEdit | None:
    if not claim.content_lines:
        return None

    forbidden_sequence = [
        normalize_line_endings(line)
        for line in claim.content_lines
    ]
    position = _baseline_reference_absence_position(
        claim.baseline_reference,
        working_lines,
        len(forbidden_sequence),
    )
    if position is None:
        return None
    if not _line_slice_equals(working_lines, position, forbidden_sequence):
        return None
    return position, position + len(forbidden_sequence), []


def _apply_non_overlapping_baseline_edits(
    working_lines: Sequence[bytes],
    edits: list[_BaselineLineEdit],
) -> Iterator[bytes] | None:
    sorted_edits = sorted(edits, key=lambda edit: (edit[0], edit[1]))
    previous_end = 0
    for start, end, _replacement_lines in sorted_edits:
        if start < previous_end:
            return None
        previous_end = max(previous_end, end)

    return _iter_lines_with_baseline_edits(working_lines, sorted_edits)


def _iter_lines_with_baseline_edits(
    working_lines: Sequence[bytes],
    sorted_edits: Sequence[_BaselineLineEdit],
) -> Iterator[bytes]:
    position = 0
    for start, end, replacement_lines in sorted_edits:
        for index in range(position, start):
            yield working_lines[index]
        yield from replacement_lines
        position = end

    for index in range(position, len(working_lines)):
        yield working_lines[index]


def _has_complete_baseline_references(
    ownership: 'BatchOwnership',
    presence_line_set: set[int],
    deletion_claims: list['DeletionClaim'],
) -> bool:
    claimed_line_references = ownership.presence_baseline_references()
    for claimed_line in presence_line_set:
        reference = claimed_line_references.get(claimed_line)
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not getattr(reference, "has_after_line", False):
            return False
    return bool(presence_line_set or deletion_claims)


def _try_apply_baseline_replacement_units(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    presence_line_set: set[int],
    deletion_claims: list['DeletionClaim'],
) -> Iterator[bytes] | None:
    """Apply baseline-coordinate edits when structural source anchors fail.

    This is a conservative fallback for same-source round trips where the batch
    source is the post-change file and the target is still the pre-change
    baseline/index. In that shape, source anchors can legitimately be absent
    even though the old baseline bytes still exist at an exact recorded
    coordinate.
    """
    if any(claimed_line < 1 or claimed_line > len(source_lines) for claimed_line in presence_line_set):
        return None

    if (
        _line_sequences_equal(source_lines, working_lines)
        and _has_complete_baseline_references(
            ownership,
            presence_line_set,
            deletion_claims,
        )
    ):
        return iter(working_lines)

    replacement_units = getattr(ownership, "replacement_units", [])
    edits: list[_BaselineLineEdit] = []
    unit_claimed_lines: set[int] = set()
    unit_deletion_indices: set[int] = set()

    for unit in replacement_units:
        claimed_lines = sorted(parse_line_selection(",".join(unit.presence_lines))) if unit.presence_lines else []
        if not claimed_lines or len(unit.deletion_indices) != 1:
            return None

        deletion_index = unit.deletion_indices[0]
        if deletion_index < 0 or deletion_index >= len(deletion_claims):
            return None
        replacement_lines: list[bytes] = []
        for claimed_line in claimed_lines:
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            replacement_lines.append(source_lines[claimed_line - 1])

        removal_edit = _baseline_removal_edit(
            deletion_claims[deletion_index],
            working_lines,
        )
        if removal_edit is None:
            return None
        start, end, _removed_lines = removal_edit
        edits.append((start, end, replacement_lines))
        unit_claimed_lines.update(claimed_lines)
        unit_deletion_indices.add(deletion_index)

    for deletion_index, claim in enumerate(deletion_claims):
        if deletion_index in unit_deletion_indices:
            continue
        removal_edit = _baseline_removal_edit(claim, working_lines)
        if removal_edit is None:
            return None
        edits.append(removal_edit)

    remaining_claimed_lines = presence_line_set - unit_claimed_lines
    claimed_line_references = ownership.presence_baseline_references()
    if remaining_claimed_lines:
        grouped_insertions: dict[int, list[int]] = {}
        for claimed_line in sorted(remaining_claimed_lines):
            if claimed_line < 1 or claimed_line > len(source_lines):
                return None
            reference = claimed_line_references.get(claimed_line)
            position = _baseline_reference_insertion_position(
                reference,
                working_lines,
            )
            if position is None:
                return None
            grouped_insertions.setdefault(position, []).append(claimed_line)

        for position, claimed_lines in grouped_insertions.items():
            insertion_lines = [
                source_lines[claimed_line - 1]
                for claimed_line in claimed_lines
            ]
            if _line_slice_equals(working_lines, position, insertion_lines):
                continue
            edits.append((
                position,
                position,
                insertion_lines,
            ))

    if unit_claimed_lines | remaining_claimed_lines != presence_line_set:
        return None

    return _apply_non_overlapping_baseline_edits(working_lines, edits)


def merge_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
) -> EditorBuffer:
    """Merge line sequences and return a buffer with destination line endings."""
    result_line_ending = _merge_result_line_ending_from_lines(
        working_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    return EditorBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _merge_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                source_to_working_mapping=source_to_working_mapping,
            ),
            result_line_ending,
        )
    )


def can_merge_batch_from_line_sequences(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
) -> bool:
    """Return whether a normalized line merge can be applied."""
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    try:
        for _chunk in _merge_batch_line_chunks(
            normalized_source_lines,
            ownership,
            normalized_working_lines,
            source_to_working_mapping=source_to_working_mapping,
        ):
            pass
    except MergeError:
        return False
    return True


def _merge_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    *,
    source_to_working_mapping: LineMapping | None = None,
) -> Iterator[bytes]:
    """Merge normalized byte-line sequences and yield normalized chunks."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
    ):
        yield from _merge_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            source_to_working_mapping=source_to_working_mapping,
        )


def _merge_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    *,
    source_to_working_mapping: LineMapping | None = None,
) -> Iterator[bytes]:
    """Merge acquired normalized line sequences and yield normalized chunks."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    fallback_chunks = _try_apply_baseline_replacement_units(
        source_lines,
        working_lines,
        ownership,
        presence_line_set,
        deletion_claims,
    )
    if fallback_chunks is not None:
        yield from _byte_chunks(fallback_chunks)
        return

    owned_mapping: LineMapping | None = None
    mapping = source_to_working_mapping
    if mapping is None:
        owned_mapping = match_lines(source_lines, working_lines)
        mapping = owned_mapping
    try:
        _check_structural_validity(
            mapping,
            presence_line_set,
            deletion_claims,
            source_lines,
            working_lines
        )

        realized_entries = _satisfy_constraints(
            source_lines,
            working_lines,
            presence_line_set,
            deletion_claims,
            source_to_working_mapping=mapping,
        )
    except MergeError:
        fallback_chunks = _try_apply_baseline_replacement_units(
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
        )
        if fallback_chunks is not None:
            yield from _byte_chunks(fallback_chunks)
            return
        raise
    finally:
        if owned_mapping is not None:
            owned_mapping.close()

    yield from _realized_entry_content_chunks(realized_entries)


def discard_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> EditorBuffer:
    """Discard ownership and return a buffer with destination line endings."""
    result_line_ending = _discard_result_line_ending_from_lines(
        working_lines,
        baseline_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    normalized_baseline_lines = normalize_line_sequence_endings(baseline_lines)
    return EditorBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _discard_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                normalized_baseline_lines,
            ),
            result_line_ending,
        ),
    )


def _discard_batch_line_chunks(
    source_lines: AcquirableLineSequence[Any],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[Any],
    baseline_lines: AcquirableLineSequence[Any],
) -> Iterator[bytes]:
    """Discard ownership from normalized byte-line sequences."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
        baseline_lines.acquire_lines() as acquired_baseline_lines,
    ):
        yield from _discard_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            acquired_baseline_lines,
        )


def _discard_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> Iterator[bytes]:
    """Discard ownership from acquired normalized byte-line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    with match_lines(source_lines, working_lines) as working_to_source:
        correspondence = _build_baseline_correspondence(
            baseline_lines,
            source_lines
        )

        realized_entries = _build_realized_entries_for_discard(
            source_lines,
            working_lines,
            working_to_source
        )

    realized_entries = _reverse_presence_constraints(
        realized_entries,
        presence_line_set,
        correspondence
    )

    realized_entries = _restore_absence_constraints(
        realized_entries,
        deletion_claims
    )

    yield from _realized_entry_content_chunks(realized_entries)


def _build_baseline_correspondence(
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes]
) -> BaselineCorrespondence:
    """Build restoration correspondence from source lines to baseline regions.

    This is a discard-specific helper that maps source lines to baseline restoration
    regions. It uses the same conservative structural matching policy as
    source-to-working provenance, then classifies the gaps between mapped anchor
    runs.

    Key distinction from match_lines:
    - match_lines: "which source lines are definitely present in working?"
    - This helper: "what baseline content restores each source position?"

    Region kinds:
    - EQUAL: unchanged lines → restored line-by-line
    - INSERT: source-only (batch added) → removed during discard
    - REPLACE_LINE_BY_LINE: changed region (same size) → restored line-by-line
    - REPLACE_BY_HUNK: changed region (different sizes) → restored as whole unit

    Replace regions are subdivided when possible:
    - If baseline and source have same number of lines: REPLACE_LINE_BY_LINE
    - If sizes differ: REPLACE_BY_HUNK (must restore entire baseline block)

    For by-hunk replace regions, discard requires full ownership:
    - If batch owns entire source-side region → restore entire baseline block
    - If batch owns only part → raise MergeError (partial discard not safe)

    Args:
        baseline_lines: Baseline file lines (bytes with newlines)
        source_lines: Batch source file lines (bytes with newlines)

    Returns:
        BaselineCorrespondence mapping source lines to restoration regions
    """
    regions: list[BaselineRegion] = []
    state = _BaselineCorrespondenceScanState()

    with match_lines(baseline_lines, source_lines) as mapping:
        for baseline_index in range(len(baseline_lines)):
            source_line = mapping.get_target_line_from_source_line(baseline_index + 1)
            if source_line is None:
                continue

            source_index = source_line - 1

            if not state.has_run:
                state = _start_baseline_anchor_run(
                    state,
                    baseline_index,
                    source_index,
                )
                continue

            if (
                baseline_index == state.run_base_end
                and source_index == state.run_source_end
            ):
                state = _extend_baseline_anchor_run(state)
                continue

            state = _flush_baseline_anchor_run(
                regions,
                state,
                baseline_lines,
                source_lines,
            )
            state = _start_baseline_anchor_run(
                state,
                baseline_index,
                source_index,
            )

    state = _flush_baseline_anchor_run(
        regions,
        state,
        baseline_lines,
        source_lines,
    )
    _append_baseline_gap_region(
        regions,
        state.next_region_id,
        baseline_lines,
        source_lines,
        state.baseline_cursor,
        len(baseline_lines),
        state.source_cursor,
        len(source_lines),
    )

    return BaselineCorrespondence(regions=regions)


def _start_baseline_anchor_run(
    state: _BaselineCorrespondenceScanState,
    baseline_index: int,
    source_index: int,
) -> _BaselineCorrespondenceScanState:
    """Return state with a new pending anchor run."""
    return _BaselineCorrespondenceScanState(
        next_region_id=state.next_region_id,
        baseline_cursor=state.baseline_cursor,
        source_cursor=state.source_cursor,
        run_base_start=baseline_index,
        run_source_start=source_index,
        run_base_end=baseline_index + 1,
        run_source_end=source_index + 1,
    )


def _extend_baseline_anchor_run(
    state: _BaselineCorrespondenceScanState,
) -> _BaselineCorrespondenceScanState:
    """Return state with the pending anchor run extended by one pair."""
    return _BaselineCorrespondenceScanState(
        next_region_id=state.next_region_id,
        baseline_cursor=state.baseline_cursor,
        source_cursor=state.source_cursor,
        run_base_start=state.run_base_start,
        run_source_start=state.run_source_start,
        run_base_end=state.run_base_end + 1,
        run_source_end=state.run_source_end + 1,
    )


def _flush_baseline_anchor_run(
    regions: list[BaselineRegion],
    state: _BaselineCorrespondenceScanState,
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> _BaselineCorrespondenceScanState:
    """Append gap/equal regions for a pending anchor run and advance cursors."""
    if not state.has_run:
        return state

    assert state.run_base_start is not None
    assert state.run_source_start is not None

    next_region_id = _append_baseline_gap_region(
        regions,
        state.next_region_id,
        baseline_lines,
        source_lines,
        state.baseline_cursor,
        state.run_base_start,
        state.source_cursor,
        state.run_source_start,
    )
    next_region_id = _append_baseline_region(
        regions,
        next_region_id,
        baseline_lines,
        state.run_base_start,
        state.run_base_end,
        state.run_source_start,
        state.run_source_end,
        RegionKind.EQUAL,
    )

    return _BaselineCorrespondenceScanState(
        next_region_id=next_region_id,
        baseline_cursor=state.run_base_end,
        source_cursor=state.run_source_end,
    )


def _append_baseline_gap_region(
    regions: list[BaselineRegion],
    next_region_id: int,
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
    base_start: int,
    base_end: int,
    src_start: int,
    src_end: int,
) -> int:
    """Append a source-space region for an unmatched baseline/source gap."""
    base_len = base_end - base_start
    src_len = src_end - src_start

    if base_len == 0 and src_len == 0:
        return next_region_id

    if src_len == 0:
        return next_region_id

    if base_len == 0:
        return _append_baseline_region(
            regions,
            next_region_id,
            baseline_lines,
            base_start,
            base_end,
            src_start,
            src_end,
            RegionKind.INSERT,
        )

    if base_len == src_len:
        baseline_segment = _LineRange(baseline_lines, base_start, base_end)
        source_segment = _LineRange(source_lines, src_start, src_end)

        with match_lines(baseline_segment, source_segment) as sub_mapping:
            all_baseline_mapped = all(
                sub_mapping.get_target_line_from_source_line(index + 1) is not None
                for index in range(len(baseline_segment))
            )
            all_source_mapped = all(
                sub_mapping.get_source_line_from_target_line(index + 1) is not None
                for index in range(len(source_segment))
            )

        kind = (
            RegionKind.REPLACE_LINE_BY_LINE
            if all_baseline_mapped and all_source_mapped
            else RegionKind.REPLACE_BY_HUNK
        )
        return _append_baseline_region(
            regions,
            next_region_id,
            baseline_lines,
            base_start,
            base_end,
            src_start,
            src_end,
            kind,
        )

    return _append_baseline_region(
        regions,
        next_region_id,
        baseline_lines,
        base_start,
        base_end,
        src_start,
        src_end,
        RegionKind.REPLACE_BY_HUNK,
    )


def _append_baseline_region(
    regions: list[BaselineRegion],
    next_region_id: int,
    baseline_lines: Sequence[bytes],
    base_start: int,
    base_end: int,
    src_start: int,
    src_end: int,
    kind: RegionKind,
) -> int:
    """Append one baseline correspondence region."""
    baseline_region_lines: Sequence[bytes]

    if src_start == src_end:
        return next_region_id

    if kind == RegionKind.INSERT:
        baseline_region_lines = ()
    else:
        baseline_region_lines = _LineRange(baseline_lines, base_start, base_end)

    regions.append(
        BaselineRegion(
            source_start_line=src_start + 1,
            source_end_line=src_end,
            baseline_lines=baseline_region_lines,
            kind=kind,
            region_id=next_region_id,
        )
    )
    return next_region_id + 1


def _build_realized_entries_for_discard(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    working_to_source: 'LineMapping'
) -> _RealizedEntries:
    """Build structured entries from working tree with source provenance.

    This creates a realized representation of the current working tree content,
    tagging each entry with its source-space provenance (if any). This allows
    subsequent discard operations to reason about which entries are batch-owned.

    Args:
        source_lines: Batch source lines (bytes with newlines)
        working_lines: Working tree lines (bytes with newlines)
        working_to_source: Mapping from source to working tree

    Returns:
        Realized entries representing working tree with source provenance
    """
    result = _RealizedEntries()

    for working_idx in range(len(working_lines)):
        source_line = working_to_source.get_source_line_from_target_line(working_idx + 1)
        result.append_line_from(
            working_lines,
            working_idx,
            source_line=source_line,
            target_line=working_idx + 1,
            is_claimed=False
        )

    return result


def _count_lines_in_range(line_set: set[int], start_line: int, end_line: int) -> int:
    line_count = end_line - start_line + 1
    if line_count <= len(line_set):
        return sum(
            1
            for line_number in range(start_line, end_line + 1)
            if line_number in line_set
        )

    return sum(
        1
        for line_number in line_set
        if start_line <= line_number <= end_line
    )


def _reverse_presence_constraints(
    entries: Sequence[RealizedEntry],
    presence_line_set: set[int],
    correspondence: BaselineCorrespondence
) -> _RealizedEntries:
    """Reverse presence constraints: replace/remove batch-owned claimed lines.

    For each entry in the working tree that corresponds to a claimed source line:
    - If from EQUAL or REPLACE_LINE_BY_LINE region: replace with baseline line-by-line
    - If from INSERT region: remove (batch-added content)
    - If from REPLACE_BY_HUNK region: verify full ownership, then restore as unit

    This is the inverse of presence constraint application: where merge ensures
    claimed lines are present, discard ensures they are removed or restored to baseline.

    Replace regions are handled intelligently:
    - Line-by-line replace (same size): restored line-by-line like equal regions
    - By-hunk replace (different sizes): requires full region ownership
      - If batch owns entire region → restore entire baseline block
      - If batch owns only part → raise MergeError (cannot safely discard partial)

    Args:
        entries: Realized entries from working tree with source provenance
        presence_line_set: Set of source line numbers that are batch-owned
        correspondence: Baseline restoration correspondence

    Returns:
        Entries with batch-owned claimed lines replaced or removed

    Raises:
        MergeError: If the restoration region is missing or cannot be
            partially discarded
    """
    result = _RealizedEntries()
    processed_replace_regions: set[int] = set()

    for index in range(len(entries)):
        source_line = _entry_source_line_at(entries, index)
        if source_line is not None and source_line in presence_line_set:
            region = correspondence.get_region_for_source_line(source_line)

            if region is None:
                raise MergeError(
                    _("Cannot discard source line {line}: no baseline restoration region found").format(
                        line=source_line
                    )
                )

            if region.kind in (RegionKind.EQUAL, RegionKind.REPLACE_LINE_BY_LINE):
                offset = source_line - region.source_start_line
                if 0 <= offset < len(region.baseline_lines):
                    result.append_line_from(
                        region.baseline_lines,
                        offset,
                        source_line=None,
                        is_claimed=False
                    )
                else:
                    raise MergeError(
                        _("Source line {line} offset {offset} outside region bounds").format(
                            line=source_line, offset=offset
                        )
                    )

            elif region.kind == RegionKind.INSERT:
                pass

            elif region.kind == RegionKind.REPLACE_BY_HUNK:
                if region.region_id not in processed_replace_regions:
                    total_lines_in_region = (
                        region.source_end_line - region.source_start_line + 1
                    )
                    claimed_line_count = _count_lines_in_range(
                        presence_line_set,
                        region.source_start_line,
                        region.source_end_line
                    )

                    if claimed_line_count != total_lines_in_region:
                        raise MergeError(
                            _("Cannot discard partial ownership of by-hunk replace region "
                              "(source lines {start}-{end}): batch owns {owned} of {total} lines").format(
                                start=region.source_start_line,
                                end=region.source_end_line,
                                owned=claimed_line_count,
                                total=total_lines_in_region
                            )
                        )

                    for baseline_idx in range(len(region.baseline_lines)):
                        result.append_line_from(
                            region.baseline_lines,
                            baseline_idx,
                            source_line=None,
                            is_claimed=False
                        )
                    processed_replace_regions.add(region.region_id)

            else:
                raise MergeError(
                    _("Unknown region kind: {kind}").format(kind=region.kind)
                )

        else:
            result.append_from(entries, index)

    return result


def _restore_absence_constraints(
    entries: Sequence[RealizedEntry],
    deletion_claims: list['DeletionClaim']
) -> _RealizedEntries:
    """Restore absence constraints: insert deleted sequences at anchored boundaries.

    For each deletion claim, this function:
    1. Finds the exact boundary "after source line N" (or start-of-file)
    2. Checks if the deleted sequence is already present at that boundary
    3. If absent: inserts it at the exact boundary
    4. If present: no-op (already restored)
    5. If anchor not present: skip gracefully (claim not applicable)
    6. If anchor is ambiguous: raise error (structural problem)

    This is the inverse of absence constraint enforcement: where merge suppresses
    sequences at anchored boundaries, discard restores them.

    Anchor handling:
    - Missing anchor: Skip claim gracefully.
    - Ambiguous anchor: Raise AmbiguousAnchorError.

    Args:
        entries: Realized entries with source provenance
        deletion_claims: Absence constraints to restore

    Returns:
        Entries with deleted sequences restored at anchored boundaries

    Raises:
        AmbiguousAnchorError: If anchor boundary is ambiguous
        (MissingAnchorError is caught and skipped gracefully)
    """
    result = _as_realized_entries(entries)
    if not deletion_claims:
        return result

    for claim in deletion_claims:
        try:
            boundary = _find_boundary_after_source_line(result, claim.anchor_line)
        except MissingAnchorError:
            continue
        except AmbiguousAnchorError:
            raise

        if _sequence_present_at_boundary(result, boundary, claim.content_lines):
            continue

        restored_entries = _RealizedEntries()
        for line_index in range(len(claim.content_lines)):
            restored_entries.append_line_from(
                claim.content_lines,
                line_index,
                source_line=None,
                is_claimed=False,
            )

        result = result.insert_entries(boundary, restored_entries)

    return result


def _sequence_present_at_boundary(
    entries: Sequence[RealizedEntry],
    boundary: int,
    sequence: list[bytes]
) -> bool:
    """Check if a byte sequence is present at the exact boundary position.

    Normalizes both entry content and sequence elements to LF line endings
    for consistent comparison across CRLF/LF representations.

    Args:
        entries: Realized entries
        boundary: Boundary position (0-indexed)
        sequence: Byte sequence to check for

    Returns:
        True if sequence is present at boundary, False otherwise
    """
    if boundary + len(sequence) > len(entries):
        return False

    return all(
        _normalize_line_content(_entry_content_at(entries, boundary + i))
        == normalize_line_endings(sequence[i])
        for i in range(len(sequence))
    )
