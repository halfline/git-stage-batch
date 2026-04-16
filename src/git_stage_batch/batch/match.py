"""Line matching and alignment between batch source and working tree."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TypeVar


LineContent = TypeVar("LineContent", bytes, str)


@dataclass
class LineMapping:
    """Alignment between batch source lines and working tree lines."""

    source_to_target: dict[int, int | None]
    target_to_source: dict[int, int | None]

    def is_source_line_present(
        self,
        source_line: int
    ) -> bool:
        """Check if a batch source line is present in working tree."""
        return self.source_to_target.get(source_line) is not None

    def get_target_line_from_source_line(
        self,
        source_line: int
    ) -> int | None:
        """Map batch source line to working tree line."""
        return self.source_to_target.get(source_line)

    def get_source_line_from_target_line(
        self,
        target_line: int
    ) -> int | None:
        """Map working tree line to batch source line."""
        return self.target_to_source.get(target_line)


def _build_unique_position_map(
    lines: list[LineContent],
    start: int,
    end: int
) -> dict[LineContent, int]:
    """Return content -> absolute index for lines that appear exactly once.

    Args:
        lines: Full line list.
        start: Inclusive segment start (0-based).
        end: Exclusive segment end (0-based).

    Returns:
        Mapping for uniquely occurring lines within the segment.
    """
    positions: dict[LineContent, list[int]] = defaultdict(list)
    unique_positions: dict[LineContent, int]
    index: int
    content: LineContent
    content_positions: list[int]

    for index in range(start, end):
        positions[lines[index]].append(index)

    unique_positions = {}
    for content, content_positions in positions.items():
        if len(content_positions) == 1:
            unique_positions[content] = content_positions[0]

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
    source_lines: list[LineContent],
    target_lines: list[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: dict[int, int | None],
    target_to_source: dict[int, int | None]
) -> tuple[int, int]:
    """Map equal prefix lines and return new segment starts."""
    while (
        source_start < source_end
        and target_start < target_end
        and source_lines[source_start] == target_lines[target_start]
    ):
        source_to_target[source_start + 1] = target_start + 1
        target_to_source[target_start + 1] = source_start + 1
        source_start += 1
        target_start += 1

    return source_start, target_start


def _map_equal_suffix(
    source_lines: list[LineContent],
    target_lines: list[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: dict[int, int | None],
    target_to_source: dict[int, int | None]
) -> tuple[int, int]:
    """Map equal suffix lines and return new segment ends."""
    while (
        source_start < source_end
        and target_start < target_end
        and source_lines[source_end - 1] == target_lines[target_end - 1]
    ):
        source_to_target[source_end] = target_end
        target_to_source[target_end] = source_end
        source_end -= 1
        target_end -= 1

    return source_end, target_end


def _mark_unmapped_segment(
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: dict[int, int | None],
    target_to_source: dict[int, int | None]
) -> None:
    """Mark all remaining lines in the segment as unmapped."""
    source_index: int
    target_index: int

    for source_index in range(source_start, source_end):
        source_to_target.setdefault(source_index + 1, None)

    for target_index in range(target_start, target_end):
        target_to_source.setdefault(target_index + 1, None)


def _align_segment(
    source_lines: list[LineContent],
    target_lines: list[LineContent],
    source_start: int,
    source_end: int,
    target_start: int,
    target_end: int,
    source_to_target: dict[int, int | None],
    target_to_source: dict[int, int | None]
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
    source_unique: dict[LineContent, int]
    target_unique: dict[LineContent, int]
    candidate_pairs: list[tuple[int, int]]
    content: LineContent
    source_index: int
    target_index: int | None
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
        _mark_unmapped_segment(
            source_start,
            source_end,
            target_start,
            target_end,
            source_to_target,
            target_to_source
        )
        return

    source_unique = _build_unique_position_map(source_lines, source_start, source_end)
    target_unique = _build_unique_position_map(target_lines, target_start, target_end)

    candidate_pairs = []
    for content, source_index in source_unique.items():
        target_index = target_unique.get(content)
        if target_index is not None:
            candidate_pairs.append((source_index, target_index))

    candidate_pairs.sort()
    anchors = _longest_increasing_subsequence(candidate_pairs)

    if not anchors:
        _mark_unmapped_segment(
            source_start,
            source_end,
            target_start,
            target_end,
            source_to_target,
            target_to_source
        )
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

        source_to_target[anchor_source + 1] = anchor_target + 1
        target_to_source[anchor_target + 1] = anchor_source + 1

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
    source_lines: list[LineContent],
    target_lines: list[LineContent]
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
    source_to_target: dict[int, int | None]
    target_to_source: dict[int, int | None]
    source_index: int
    target_index: int

    source_to_target = {}
    target_to_source = {}

    _align_segment(
        source_lines,
        target_lines,
        0,
        len(source_lines),
        0,
        len(target_lines),
        source_to_target,
        target_to_source
    )

    for source_index in range(len(source_lines)):
        source_to_target.setdefault(source_index + 1, None)

    for target_index in range(len(target_lines)):
        target_to_source.setdefault(target_index + 1, None)

    return LineMapping(
        source_to_target=source_to_target,
        target_to_source=target_to_source
    )
