"""Line matching and alignment between batch source and working tree."""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from ..utils.text import AcquirableLineSequence, as_acquirable_line_sequence


LineContent = TypeVar("LineContent", bound=Hashable)
_MAX_UINT32 = (1 << 32) - 1


@dataclass(frozen=True, slots=True)
class UniqueLinePosition:
    """Position of a line that appears exactly once in a segment."""

    index: int


@dataclass(slots=True)
class _LineOccurrenceState:
    """Occurrence state for one line content while scanning a segment."""

    first_index: int
    is_unique: bool = True


@dataclass
class LineMapping:
    """Alignment between batch source lines and working tree lines."""

    source_to_target: array
    target_to_source: array

    def is_source_line_present(
        self,
        source_line: int
    ) -> bool:
        """Check if a batch source line is present in working tree."""
        return _lookup_line_mapping(self.source_to_target, source_line) is not None

    def get_target_line_from_source_line(
        self,
        source_line: int
    ) -> int | None:
        """Map batch source line to working tree line."""
        return _lookup_line_mapping(self.source_to_target, source_line)

    def get_source_line_from_target_line(
        self,
        target_line: int
    ) -> int | None:
        """Map working tree line to batch source line."""
        return _lookup_line_mapping(self.target_to_source, target_line)


def _line_mapping_typecode(max_line_number: int) -> str:
    if max_line_number <= _MAX_UINT32:
        return "I"
    return "Q"


def _new_line_mapping(size: int, max_line_number: int) -> array:
    return array(_line_mapping_typecode(max_line_number), [0]) * size


def _lookup_line_mapping(mapping: array, line_number: int) -> int | None:
    if line_number < 1 or line_number > len(mapping):
        return None

    mapped_line = mapping[line_number - 1]
    if mapped_line == 0:
        return None
    return mapped_line


def _build_unique_position_map(
    lines: Sequence[LineContent],
    start: int,
    end: int
) -> dict[LineContent, UniqueLinePosition]:
    """Return content -> position for lines that appear exactly once.

    Args:
        lines: Full line sequence.
        start: Inclusive segment start (0-based).
        end: Exclusive segment end (0-based).

    Returns:
        Mapping for uniquely occurring lines within the segment.
    """
    occurrences: dict[LineContent, _LineOccurrenceState]
    unique_positions: dict[LineContent, UniqueLinePosition]
    index: int
    content: LineContent
    occurrence: _LineOccurrenceState

    occurrences = {}

    for index in range(start, end):
        content = lines[index]
        try:
            occurrence = occurrences[content]
        except KeyError:
            occurrences[content] = _LineOccurrenceState(first_index=index)
        else:
            occurrence.is_unique = False

    unique_positions = {}
    for content, occurrence in occurrences.items():
        if occurrence.is_unique:
            unique_positions[content] = UniqueLinePosition(
                index=occurrence.first_index
            )

    return unique_positions


def _is_better_lis_entry(
    candidate: tuple[int, int],
    current: tuple[int, int]
) -> bool:
    """Prefer longer subsequences, then earlier source-ordered endings."""
    candidate_length: int
    candidate_index: int
    current_length: int
    current_index: int

    candidate_length, candidate_index = candidate
    current_length, current_index = current

    if candidate_length != current_length:
        return candidate_length > current_length

    if candidate_length == 0:
        return False

    return candidate_index < current_index


def _query_best_by_target_rank(
    best_by_target_rank: list[tuple[int, int]],
    target_rank: int
) -> tuple[int, int]:
    """Return the best LIS ending before a target rank."""
    best: tuple[int, int]
    candidate: tuple[int, int]

    best = (0, -1)

    while target_rank > 0:
        candidate = best_by_target_rank[target_rank]
        if _is_better_lis_entry(candidate, best):
            best = candidate
        target_rank -= target_rank & -target_rank

    return best


def _update_best_by_target_rank(
    best_by_target_rank: list[tuple[int, int]],
    target_rank: int,
    candidate: tuple[int, int]
) -> None:
    """Record a candidate LIS ending at a target rank."""
    while target_rank < len(best_by_target_rank):
        if _is_better_lis_entry(candidate, best_by_target_rank[target_rank]):
            best_by_target_rank[target_rank] = candidate
        target_rank += target_rank & -target_rank


def _longest_increasing_subsequence(
    pairs: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Return a longest increasing subsequence by target index.

    Uses an O(n log n) best_by_target_rank table.
    Equal-length ties keep the earliest source-ordered pair.

    Args:
        pairs: Candidate (source_index, target_index) pairs sorted by source index.

    Returns:
        Monotonic anchor pairs preserving both source and target order.
    """
    target_indices: list[int]
    best_by_target_rank: list[tuple[int, int]]
    predecessors: list[int | None]
    best_length: int
    best_index: int
    pair_index: int
    target_index: int
    target_rank: int
    predecessor_length: int
    predecessor_index: int
    current_length: int
    result: list[tuple[int, int]]
    index: int | None

    if not pairs:
        return []

    target_indices = sorted({target_index for _, target_index in pairs})
    best_by_target_rank = [(0, -1)] * (len(target_indices) + 1)
    predecessors = [None] * len(pairs)
    best_length = 0
    best_index = 0

    for pair_index, (_, target_index) in enumerate(pairs):
        target_rank = bisect_left(target_indices, target_index) + 1
        predecessor_length, predecessor_index = _query_best_by_target_rank(
            best_by_target_rank,
            target_rank - 1
        )
        current_length = predecessor_length + 1

        if predecessor_index >= 0:
            predecessors[pair_index] = predecessor_index

        if current_length > best_length:
            best_length = current_length
            best_index = pair_index

        _update_best_by_target_rank(
            best_by_target_rank,
            target_rank,
            (current_length, pair_index)
        )

    result = []
    index = best_index

    while index is not None:
        result.append(pairs[index])
        index = predecessors[index]

    result.reverse()
    return result


def _map_equal_prefix(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: array,
    target_to_source: array
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
    source_to_target: array,
    target_to_source: array
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
    source_to_target: array,
    target_to_source: array
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
    source_unique: dict[LineContent, UniqueLinePosition]
    target_unique: dict[LineContent, UniqueLinePosition]
    candidate_pairs: list[tuple[int, int]]
    content: LineContent
    source_position: UniqueLinePosition
    target_position: UniqueLinePosition | None
    anchors: list[tuple[int, int]]
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

    source_unique = _build_unique_position_map(source_lines, source_start, source_end)
    target_unique = _build_unique_position_map(target_lines, target_start, target_end)

    candidate_pairs = []
    for content, source_position in source_unique.items():
        target_position = target_unique.get(content)
        if target_position is not None:
            candidate_pairs.append(
                (
                    source_position.index,
                    target_position.index
                )
            )

    candidate_pairs.sort()
    anchors = _longest_increasing_subsequence(candidate_pairs)

    del source_unique, target_unique, candidate_pairs

    if not anchors:
        return

    previous_source = source_start
    previous_target = target_start

    for anchor_source, anchor_target in anchors:
        _align_segment(
            source_lines,
            target_lines,
            previous_source,
            anchor_source,
            previous_target,
            anchor_target,
            source_to_target,
            target_to_source
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
        target_to_source
    )


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
    source_to_target: array
    target_to_source: array
    max_line_number: int
    source_line_count: int
    target_line_count: int

    source_line_count = len(source_lines)
    target_line_count = len(target_lines)
    max_line_number = max(source_line_count, target_line_count)
    source_to_target = _new_line_mapping(source_line_count, max_line_number)
    target_to_source = _new_line_mapping(target_line_count, max_line_number)

    with (
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
            target_to_source
        )

    return LineMapping(
        source_to_target=source_to_target,
        target_to_source=target_to_source
    )


def match_lines(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent]
) -> LineMapping:
    """Compute conservative structural alignment between source and target."""
    return match_acquirable_lines(
        as_acquirable_line_sequence(source_lines),
        as_acquirable_line_sequence(target_lines),
    )
