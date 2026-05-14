"""Line matching and alignment between batch source and working tree."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from .match_storage import MatcherWorkspace
from ..utils.mapped_storage import MappedIntVector, MappedRecordVector
from ..utils.text import AcquirableLineSequence, as_acquirable_line_sequence


LineContent = TypeVar("LineContent", bound=Hashable)
_MAX_UINT32 = (1 << 32) - 1
_MAX_UINT64 = (1 << 64) - 1
_LINE_PAIR_RECORD_FORMAT = "QQ"
_OCCURRENCE_RECORD_FORMAT = "QQQQQQ"
_OCCURRENCE_HASH = 0
_OCCURRENCE_SOURCE_INDEX = 1
_OCCURRENCE_SOURCE_COUNT = 2
_OCCURRENCE_TARGET_INDEX = 3
_OCCURRENCE_TARGET_COUNT = 4
_OCCURRENCE_NEXT = 5


class IntVector(Protocol):
    """Fixed-width integer vector used by line mappings."""

    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> int: ...

    def __setitem__(self, index: int, value: int) -> None: ...


@dataclass
class LineMapping:
    """Alignment between batch source lines and working tree lines."""

    source_to_target: IntVector
    target_to_source: IntVector
    _closed: bool = field(default=False, init=False, repr=False)
    _close_on_exit: bool = field(default=True, init=False, repr=False)

    def __enter__(self) -> LineMapping:
        self._require_open()
        self._close_on_exit = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._close_on_exit:
            self.close()

    def detach(self) -> LineMapping:
        """Transfer ownership out of the current context manager."""
        self._require_open()
        self._close_on_exit = False
        return self

    def close(self) -> None:
        """Close owned vector storage."""
        if self._closed:
            return

        _close_vector(self.source_to_target)
        _close_vector(self.target_to_source)
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def is_source_line_present(
        self,
        source_line: int
    ) -> bool:
        """Check if a batch source line is present in working tree."""
        self._require_open()
        return _lookup_line_mapping(self.source_to_target, source_line) is not None

    def get_target_line_from_source_line(
        self,
        source_line: int
    ) -> int | None:
        """Map batch source line to working tree line."""
        self._require_open()
        return _lookup_line_mapping(self.source_to_target, source_line)

    def get_source_line_from_target_line(
        self,
        target_line: int
    ) -> int | None:
        """Map working tree line to batch source line."""
        self._require_open()
        return _lookup_line_mapping(self.target_to_source, target_line)

    def mapped_line_pairs(self) -> Iterator[tuple[int, int]]:
        """Yield mapped source/target line pairs in source-line order."""
        self._require_open()
        for source_index in range(len(self.source_to_target)):
            target_line = self.source_to_target[source_index]
            if target_line != 0:
                yield source_index + 1, target_line

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("line mapping is closed")


def _close_vector(vector: IntVector) -> None:
    close = getattr(vector, "close", None)
    if close is not None:
        close()


def _line_mapping_width(max_line_number: int) -> int:
    if max_line_number <= _MAX_UINT32:
        return 4
    return 8


def _new_line_mapping(size: int, max_line_number: int) -> MappedIntVector:
    return MappedIntVector(
        size,
        width=_line_mapping_width(max_line_number),
        fill=0,
    )


def _lookup_line_mapping(mapping: IntVector, line_number: int) -> int | None:
    if line_number < 1 or line_number > len(mapping):
        return None

    mapped_line = mapping[line_number - 1]
    if mapped_line == 0:
        return None
    return mapped_line


class _LineOccurrenceTable:
    """Storage-backed occurrence table for one active source/target segment."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        source_lines: Sequence[LineContent],
        source_start: int,
        source_end: int,
    ) -> None:
        source_length = source_end - source_start
        self._workspace = workspace
        self._source_lines = source_lines
        self._bucket_count = _occurrence_bucket_capacity(source_length)
        self._buckets = workspace.int_vector(
            self._bucket_count,
            width=8,
            fill=0,
        )
        self._records = workspace.record_vector(
            source_length,
            _OCCURRENCE_RECORD_FORMAT,
        )
        self._closed = False

    def scan_source(self, start: int, end: int) -> None:
        """Record source-side occurrence counts."""
        for index in range(start, end):
            line_hash = _line_hash(self._source_lines[index])
            record_index = self._find_record(
                self._source_lines,
                index,
                line_hash,
            )
            if record_index is None:
                bucket_index = self._bucket_index(line_hash)
                next_record = self._buckets[bucket_index]
                new_record_index = self._records.append((
                    line_hash,
                    index,
                    1,
                    0,
                    0,
                    next_record,
                ))
                self._buckets[bucket_index] = new_record_index + 1
                continue

            record = self._records[record_index]
            self._records[record_index] = _replace_record_value(
                record,
                _OCCURRENCE_SOURCE_COUNT,
                min(2, record[_OCCURRENCE_SOURCE_COUNT] + 1),
            )

    def scan_target(
        self,
        target_lines: Sequence[LineContent],
        start: int,
        end: int,
    ) -> None:
        """Record target-side occurrences for source-known content."""
        for index in range(start, end):
            line_hash = _line_hash(target_lines[index])
            record_index = self._find_record(target_lines, index, line_hash)
            if record_index is None:
                continue

            record = self._records[record_index]
            target_count = record[_OCCURRENCE_TARGET_COUNT]
            if target_count == 0:
                record = _replace_record_value(
                    record,
                    _OCCURRENCE_TARGET_INDEX,
                    index,
                )
            self._records[record_index] = _replace_record_value(
                record,
                _OCCURRENCE_TARGET_COUNT,
                min(2, target_count + 1),
            )

    def emit_candidate_pairs(
        self,
        workspace: MatcherWorkspace,
        source_start: int,
        source_end: int,
    ) -> MappedRecordVector:
        """Return source-ordered candidate anchor pairs."""
        candidates = workspace.record_vector(
            source_end - source_start,
            _LINE_PAIR_RECORD_FORMAT,
        )

        for source_index in range(source_start, source_end):
            line_hash = _line_hash(self._source_lines[source_index])
            record_index = self._find_record(
                self._source_lines,
                source_index,
                line_hash,
            )
            if record_index is None:
                continue

            record = self._records[record_index]
            if (
                record[_OCCURRENCE_SOURCE_INDEX] == source_index
                and record[_OCCURRENCE_SOURCE_COUNT] == 1
                and record[_OCCURRENCE_TARGET_COUNT] == 1
            ):
                candidates.append((
                    source_index,
                    record[_OCCURRENCE_TARGET_INDEX],
                ))

        return candidates

    def close(self) -> None:
        """Release mapped occurrence storage."""
        if self._closed:
            return
        self._workspace.close_resource(self._records)
        self._workspace.close_resource(self._buckets)
        self._closed = True

    def _find_record(
        self,
        lines: Sequence[LineContent],
        index: int,
        line_hash: int,
    ) -> int | None:
        record_number = self._buckets[self._bucket_index(line_hash)]
        line = lines[index]

        while record_number != 0:
            record_index = record_number - 1
            record = self._records[record_index]
            if (
                record[_OCCURRENCE_HASH] == line_hash
                and self._source_lines[record[_OCCURRENCE_SOURCE_INDEX]] == line
            ):
                return record_index
            record_number = record[_OCCURRENCE_NEXT]

        return None

    def _bucket_index(self, line_hash: int) -> int:
        return line_hash & (self._bucket_count - 1)


def _occurrence_bucket_capacity(source_length: int) -> int:
    capacity = 1
    target_capacity = max(1, source_length * 2)
    while capacity < target_capacity:
        capacity <<= 1
    return capacity


def _line_hash(line: Hashable) -> int:
    return hash(line) & _MAX_UINT64


def _replace_record_value(
    record: tuple[int, ...],
    field_index: int,
    value: int,
) -> tuple[int, ...]:
    mutable = list(record)
    mutable[field_index] = value
    return tuple(mutable)


def _longest_increasing_subsequence_records(
    pairs: MappedRecordVector,
    target_start: int,
    target_end: int,
    workspace: MatcherWorkspace,
) -> MappedRecordVector:
    """Return mapped LIS anchors for source-ordered candidate records."""
    pair_count = len(pairs)

    if pair_count == 0 or target_end <= target_start:
        return workspace.record_vector(0, _LINE_PAIR_RECORD_FORMAT)

    target_indices = sorted({
        pairs[pair_index][1]
        for pair_index in range(pair_count)
    })
    best_lengths = workspace.int_vector(len(target_indices) + 1, width=8, fill=0)
    best_indexes = workspace.int_vector(len(target_indices) + 1, width=8, fill=0)
    predecessors = workspace.int_vector(pair_count, width=8, fill=0)
    result_indexes = None

    try:
        best_length = 0
        best_index_number = 0

        for pair_index in range(pair_count):
            _, target_index = pairs[pair_index]
            target_rank = bisect_left(target_indices, target_index) + 1
            predecessor_length, predecessor_index_number = (
                _query_best_record_by_target_rank(
                    best_lengths,
                    best_indexes,
                    target_rank - 1,
                )
            )
            current_length = predecessor_length + 1

            if predecessor_index_number != 0:
                predecessors[pair_index] = predecessor_index_number

            if current_length > best_length:
                best_length = current_length
                best_index_number = pair_index + 1

            _update_best_record_by_target_rank(
                best_lengths,
                best_indexes,
                target_rank,
                current_length,
                pair_index + 1,
            )

        anchors = workspace.record_vector(best_length, _LINE_PAIR_RECORD_FORMAT)
        if best_length == 0:
            return anchors

        result_indexes = workspace.int_vector(best_length, width=8, fill=0)
        result_offset = best_length - 1
        index_number = best_index_number

        while index_number != 0:
            result_indexes[result_offset] = index_number
            result_offset -= 1
            index_number = predecessors[index_number - 1]

        for result_offset in range(best_length):
            source_index, target_index = pairs[result_indexes[result_offset] - 1]
            anchors.append((source_index, target_index))

        return anchors
    finally:
        if result_indexes is not None:
            workspace.close_resource(result_indexes)
        workspace.close_resource(predecessors)
        workspace.close_resource(best_indexes)
        workspace.close_resource(best_lengths)


def _query_best_record_by_target_rank(
    best_lengths: IntVector,
    best_indexes: IntVector,
    target_rank: int,
) -> tuple[int, int]:
    """Return the best mapped LIS record ending before a target rank."""
    best_length = 0
    best_index_number = 0

    while target_rank > 0:
        candidate_length = best_lengths[target_rank]
        candidate_index_number = best_indexes[target_rank]
        if _is_better_lis_record_entry(
            candidate_length,
            candidate_index_number,
            best_length,
            best_index_number,
        ):
            best_length = candidate_length
            best_index_number = candidate_index_number
        target_rank -= target_rank & -target_rank

    return best_length, best_index_number


def _update_best_record_by_target_rank(
    best_lengths: IntVector,
    best_indexes: IntVector,
    target_rank: int,
    candidate_length: int,
    candidate_index_number: int,
) -> None:
    """Record a mapped LIS candidate ending at a target rank."""
    while target_rank < len(best_lengths):
        if _is_better_lis_record_entry(
            candidate_length,
            candidate_index_number,
            best_lengths[target_rank],
            best_indexes[target_rank],
        ):
            best_lengths[target_rank] = candidate_length
            best_indexes[target_rank] = candidate_index_number
        target_rank += target_rank & -target_rank


def _is_better_lis_record_entry(
    candidate_length: int,
    candidate_index_number: int,
    current_length: int,
    current_index_number: int,
) -> bool:
    """Prefer longer mapped subsequences, then earlier source endings."""
    if candidate_length != current_length:
        return candidate_length > current_length

    if candidate_length == 0:
        return False

    if current_index_number == 0:
        return True

    return candidate_index_number < current_index_number


def _map_equal_prefix(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: IntVector,
    target_to_source: IntVector
) -> tuple[int, int]:
    """Map equal prefix lines and return new segment starts."""
    while (
        source_start < source_end
        and target_start < target_end
        and source_lines[source_start] == target_lines[target_start]
    ):
        source_to_target[source_start] = target_start + 1
        target_to_source[target_start] = source_start + 1
        source_start += 1
        target_start += 1

    return source_start, target_start


def _map_equal_suffix(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: IntVector,
    target_to_source: IntVector
) -> tuple[int, int]:
    """Map equal suffix lines and return new segment ends."""
    while (
        source_start < source_end
        and target_start < target_end
        and source_lines[source_end - 1] == target_lines[target_end - 1]
    ):
        source_to_target[source_end - 1] = target_end
        target_to_source[target_end - 1] = source_end
        source_end -= 1
        target_end -= 1

    return source_end, target_end


def _align_segment(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: IntVector,
    target_to_source: IntVector,
    workspace: MatcherWorkspace,
) -> None:
    """Conservatively align one source/target segment.

    Strategy:
    - map exact equal prefix
    - map exact equal suffix
    - use lines unique within both remaining segments as anchors
    - recurse between anchors
    - if no trustworthy anchors exist, leave the region unmapped

    This function is intentionally conservative. It never guesses inside
    structurally ambiguous regions.
    """
    occurrence_table: _LineOccurrenceTable
    candidate_pairs: MappedRecordVector
    anchors: MappedRecordVector
    previous_source: int
    previous_target: int
    anchor_source: int
    anchor_target: int

    source_start, target_start = _map_equal_prefix(
        source_lines,
        target_lines,
        source_start,
        source_end,
        target_start,
        target_end,
        source_to_target,
        target_to_source
    )

    source_end, target_end = _map_equal_suffix(
        source_lines,
        target_lines,
        source_start,
        source_end,
        target_start,
        target_end,
        source_to_target,
        target_to_source
    )

    if source_start >= source_end or target_start >= target_end:
        return

    occurrence_table = _LineOccurrenceTable(
        workspace,
        source_lines,
        source_start,
        source_end,
    )
    try:
        occurrence_table.scan_source(source_start, source_end)
        occurrence_table.scan_target(target_lines, target_start, target_end)
        candidate_pairs = occurrence_table.emit_candidate_pairs(
            workspace,
            source_start,
            source_end,
        )
    finally:
        occurrence_table.close()

    try:
        anchors = _longest_increasing_subsequence_records(
            candidate_pairs,
            target_start,
            target_end,
            workspace,
        )
    finally:
        workspace.close_resource(candidate_pairs)

    if not anchors:
        workspace.close_resource(anchors)
        return

    previous_source = source_start
    previous_target = target_start

    try:
        for anchor_source, anchor_target in anchors:
            _align_segment(
                source_lines,
                target_lines,
                previous_source,
                anchor_source,
                previous_target,
                anchor_target,
                source_to_target,
                target_to_source,
                workspace,
            )

            source_to_target[anchor_source] = anchor_target + 1
            target_to_source[anchor_target] = anchor_source + 1

            previous_source = anchor_source + 1
            previous_target = anchor_target + 1

        _align_segment(
            source_lines,
            target_lines,
            previous_source,
            source_end,
            previous_target,
            target_end,
            source_to_target,
            target_to_source,
            workspace,
        )
    finally:
        workspace.close_resource(anchors)


def match_acquirable_lines(
    source_lines: AcquirableLineSequence[LineContent],
    target_lines: AcquirableLineSequence[LineContent]
) -> LineMapping:
    """Compute conservative structural alignment between source and target.

    This matcher is bytes-safe and ambiguity-intolerant.

    It only creates mappings when they are structurally trustworthy:
    - exact equal prefixes and suffixes
    - unique lines that occur exactly once in both corresponding segments
    - recursive alignment between trustworthy anchors

    Ambiguous regions are left unmapped.

    This is not trying to maximize matches. It is trying to avoid false matches.

    Args:
        source_lines: Batch source file lines.
        target_lines: Working tree file lines.

    Returns:
        A bidirectional line mapping with ambiguous lines left unmapped.
    """
    source_to_target: MappedIntVector
    target_to_source: MappedIntVector
    workspace: MatcherWorkspace
    max_line_number: int
    source_line_count: int
    target_line_count: int

    source_line_count = len(source_lines)
    target_line_count = len(target_lines)
    max_line_number = max(source_line_count, target_line_count)
    source_to_target = _new_line_mapping(source_line_count, max_line_number)
    target_to_source = _new_line_mapping(target_line_count, max_line_number)

    try:
        with (
            MatcherWorkspace() as workspace,
            source_lines.acquire_lines() as acquired_source_lines,
            target_lines.acquire_lines() as acquired_target_lines,
        ):
            _align_segment(
                acquired_source_lines,
                acquired_target_lines,
                0,
                source_line_count,
                0,
                target_line_count,
                source_to_target,
                target_to_source,
                workspace,
            )

        return LineMapping(
            source_to_target=source_to_target,
            target_to_source=target_to_source
        )
    except Exception:
        source_to_target.close()
        target_to_source.close()
        raise


def match_lines(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent]
) -> LineMapping:
    """Compute conservative structural alignment between source and target."""
    return match_acquirable_lines(
        as_acquirable_line_sequence(source_lines),
        as_acquirable_line_sequence(target_lines),
    )
