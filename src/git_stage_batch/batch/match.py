"""Line matching and alignment between batch source and working tree."""

from __future__ import annotations

from array import array
from collections.abc import Hashable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, TypeVar


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


def _acquire_line_sequence(lines: Sequence[LineContent]) -> Any:
    acquire_lines = getattr(lines, "acquire_lines", None)
    if acquire_lines is None:
        return nullcontext(lines)
    return acquire_lines()


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


def _longest_increasing_subsequence(
    pairs: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Return a longest increasing subsequence by target index.

    Args:
        pairs: Candidate (source_index, target_index) pairs sorted by source index.

    Returns:
        Monotonic anchor pairs preserving both source and target order.
    """
    lengths: list[int]
    previous: list[int | None]
    best_length: int
    best_index: int
    current: int
    candidate: int
    current_target: int
    candidate_target: int
    result: list[tuple[int, int]]
    index: int | None

    if not pairs:
        return []

    lengths = [1] * len(pairs)
    previous = [None] * len(pairs)
    best_length = 1
    best_index = 0

    for current in range(len(pairs)):
        current_target = pairs[current][1]

        for candidate in range(current):
            candidate_target = pairs[candidate][1]
            if candidate_target < current_target and lengths[candidate] + 1 > lengths[current]:
                lengths[current] = lengths[candidate] + 1
                previous[current] = candidate

        if lengths[current] > best_length:
            best_length = lengths[current]
            best_index = current

    result = []
    index = best_index

    while index is not None:
        result.append(pairs[index])
        index = previous[index]

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


def match_lines(
    source_lines: Sequence[LineContent],
    target_lines: Sequence[LineContent]
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
        _acquire_line_sequence(source_lines) as acquired_source_lines,
        _acquire_line_sequence(target_lines) as acquired_target_lines,
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
