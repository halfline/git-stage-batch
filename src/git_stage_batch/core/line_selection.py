"""Line selection parsing and range helpers."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import chain
from typing import Iterable, Iterator, Protocol

from ..i18n import _


class LineSelection(Protocol):
    """Positive 1-based line selection with cheap range operations."""

    def __contains__(self, line_number: object) -> bool:
        ...

    def __bool__(self) -> bool:
        ...

    def __iter__(self) -> Iterator[int]:
        ...

    def ranges(self) -> tuple[tuple[int, int], ...]:
        """Return normalized inclusive ranges."""
        ...

    def count(self, start: int | None = None, end: int | None = None) -> int:
        """Return selected-line count, optionally inside an inclusive range."""
        ...

    def intersection(self, other: LineSelection | Iterable[int]) -> LineRanges:
        """Return selected lines also present in another selection."""
        ...


def _normalize_line_ranges(
    ranges: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    sorted_ranges = sorted(ranges)
    if not sorted_ranges:
        return ()

    normalized: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None

    for start, end in sorted_ranges:
        if start <= 0 or end <= 0:
            raise ValueError(
                _("Line IDs must be positive: {value}").format(
                    value=f"{start}-{end}"
                )
            )
        if start > end:
            raise ValueError(
                _("Range start must be <= end: {value}").format(
                    value=f"{start}-{end}"
                )
            )

        if current_start is None or current_end is None:
            current_start = start
            current_end = end
            continue

        if start <= current_end + 1:
            current_end = max(current_end, end)
            continue

        normalized.append((current_start, current_end))
        current_start = start
        current_end = end

    if current_start is not None and current_end is not None:
        normalized.append((current_start, current_end))

    return tuple(normalized)


def _line_ranges_count(ranges: Iterable[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in ranges)


def _selection_ranges(
    selection: LineSelection | Iterable[int],
) -> tuple[tuple[int, int], ...]:
    return coerce_line_ranges(selection).ranges()


def coerce_line_ranges(selection: LineSelection | Iterable[int]) -> LineRanges:
    """Return a range-backed line selection without expanding range inputs."""
    if isinstance(selection, LineRanges):
        return selection
    ranges = getattr(selection, "ranges", None)
    if ranges is not None:
        return LineRanges.from_ranges(ranges())
    return LineRanges.from_lines(selection)


def scan_line_range_specs(
    line_ranges: Iterable[str | int],
) -> Iterator[tuple[int, int]]:
    """Yield raw inclusive ranges from line specifications without collecting."""
    for line_range in line_ranges:
        specification = str(line_range)
        item_start = 0
        while item_start <= len(specification):
            separator = specification.find(",", item_start)
            item_stop = len(specification) if separator < 0 else separator
            item = specification[item_start:item_stop].strip()
            if item:
                range_separator = item.find("-", 1)
                if range_separator < 0:
                    try:
                        start = end = int(item)
                    except ValueError as error:
                        raise ValueError(
                            _("Invalid line ID: {value}").format(value=item)
                        ) from error
                    if start <= 0:
                        raise ValueError(
                            _("Line ID must be positive: {value}").format(
                                value=item
                            )
                        )
                else:
                    try:
                        start = int(item[:range_separator].strip())
                        end = int(item[range_separator + 1 :].strip())
                    except ValueError as error:
                        raise ValueError(
                            _("Invalid range: {value}").format(value=item)
                        ) from error
                    if start <= 0 or end <= 0:
                        raise ValueError(
                            _("Line IDs must be positive: {value}").format(
                                value=item
                            )
                        )
                if start > end:
                    raise ValueError(
                        _("Range start must be <= end: {value}").format(
                            value=item
                        )
                    )
                yield start, end

            if separator < 0:
                break
            item_start = separator + 1


@dataclass(frozen=True, slots=True)
class LineRanges:
    """Immutable 1-based line selection stored as normalized inclusive ranges."""

    _ranges: tuple[tuple[int, int], ...] = ()
    _starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _count: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ranges = _normalize_line_ranges(self._ranges)
        object.__setattr__(self, "_ranges", ranges)
        object.__setattr__(self, "_starts", tuple(start for start, _end in ranges))
        object.__setattr__(self, "_count", _line_ranges_count(ranges))

    @classmethod
    def empty(cls) -> LineRanges:
        return cls(())

    @classmethod
    def from_lines(cls, lines: Iterable[int]) -> LineRanges:
        return cls(tuple((line, line) for line in lines))

    @classmethod
    def from_ranges(cls, ranges: Iterable[tuple[int, int]]) -> LineRanges:
        return cls(tuple(ranges))

    @classmethod
    def from_specs(cls, line_ranges: Iterable[str | int]) -> LineRanges:
        remaining_specs = iter(line_ranges)
        try:
            first_spec = next(remaining_specs)
        except StopIteration:
            return cls.empty()

        if isinstance(first_spec, str) and not first_spec.strip():
            try:
                second_spec = next(remaining_specs)
            except StopIteration as error:
                raise ValueError(_("Selection string cannot be empty")) from error
            specs = chain((first_spec, second_spec), remaining_specs)
        else:
            specs = chain((first_spec,), remaining_specs)
        return cls.from_ranges(scan_line_range_specs(specs))

    def __contains__(self, line_number: object) -> bool:
        if type(line_number) is not int:
            return False
        index = bisect_right(self._starts, line_number) - 1
        if index < 0:
            return False
        _start, end = self._ranges[index]
        return line_number <= end

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __iter__(self) -> Iterator[int]:
        for start, end in self._ranges:
            yield from range(start, end + 1)

    def __len__(self) -> int:
        return self._count

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LineRanges):
            return self._ranges == other._ranges
        if isinstance(other, set):
            return set(self) == other
        return NotImplemented

    def ranges(self) -> tuple[tuple[int, int], ...]:
        return self._ranges

    def count(self, start: int | None = None, end: int | None = None) -> int:
        if start is None and end is None:
            return self._count
        if start is None or end is None:
            raise ValueError("range count requires both start and end")
        if start > end:
            return 0

        total = 0
        for range_start, range_end in self._ranges:
            if range_end < start:
                continue
            if range_start > end:
                break
            total += max(0, min(range_end, end) - max(range_start, start) + 1)
        return total

    def intersection(
        self,
        other: LineSelection | Iterable[int],
    ) -> LineRanges:
        other_ranges = _selection_ranges(other)
        intersections: list[tuple[int, int]] = []
        left_index = 0
        right_index = 0

        while left_index < len(self._ranges) and right_index < len(other_ranges):
            left_start, left_end = self._ranges[left_index]
            right_start, right_end = other_ranges[right_index]
            start = max(left_start, right_start)
            end = min(left_end, right_end)
            if start <= end:
                intersections.append((start, end))

            if left_end < right_end:
                left_index += 1
            else:
                right_index += 1

        return LineRanges.from_ranges(intersections)

    def difference(
        self,
        other: LineSelection | Iterable[int],
    ) -> LineRanges:
        other_ranges = _selection_ranges(other)
        if not self._ranges or not other_ranges:
            return self

        remaining: list[tuple[int, int]] = []
        other_index = 0
        for start, end in self._ranges:
            current_start = start

            while other_index < len(other_ranges) and other_ranges[other_index][1] < current_start:
                other_index += 1

            scan_index = other_index
            while scan_index < len(other_ranges):
                remove_start, remove_end = other_ranges[scan_index]
                if remove_start > end:
                    break
                if remove_start > current_start:
                    remaining.append((current_start, remove_start - 1))
                current_start = max(current_start, remove_end + 1)
                if current_start > end:
                    break
                scan_index += 1

            if current_start <= end:
                remaining.append((current_start, end))

        return LineRanges.from_ranges(remaining)

    def union(
        self,
        other: LineSelection | Iterable[int],
    ) -> LineRanges:
        return LineRanges.from_ranges((*self._ranges, *_selection_ranges(other)))

    def first(self) -> int | None:
        if not self._ranges:
            return None
        return self._ranges[0][0]

    def to_line_spec(self) -> str:
        return ",".join(
            str(start) if start == end else f"{start}-{end}"
            for start, end in self._ranges
        )

    def to_range_strings(self) -> list[str]:
        spec = self.to_line_spec()
        return [spec] if spec else []


def parse_positive_selection(
    selection: str,
    *,
    item_name: str | None = None,
    item_name_plural: str | None = None,
    reject_empty_items: bool = False,
) -> list[int]:
    """Parse a positive integer selection string into sorted unique IDs.

    Supports:
    - Individual IDs: "1,2,3" → [1, 2, 3]
    - Ranges: "5-7" → [5, 6, 7]
    - Mixed: "1,3,5-7" → [1, 3, 5, 6, 7]

    Args:
        selection: Comma-separated IDs and/or ranges (e.g., "1,3,5-7")
        item_name: Name used in error messages.
        reject_empty_items: If True, reject empty comma-separated items.

    Returns:
        Sorted list of unique IDs

    Raises:
        ValueError: If the selection string is invalid
    """
    invalid_item_name: str
    if item_name is None:
        item_name = _("Line ID")
        invalid_item_name = _("line ID")
        item_name_plural = _("Line IDs")
    else:
        invalid_item_name = item_name
        if item_name_plural is None:
            item_name_plural = item_name

    if not selection or not selection.strip():
        raise ValueError(_("Selection string cannot be empty"))

    selected_ids: set[int] = set()
    parts = selection.split(",")

    for part in parts:
        part = part.strip()
        if not part:
            if reject_empty_items:
                raise ValueError(_("Selection contains an empty item"))
            continue

        # Check if this looks like a range (contains "-" that's not at the start)
        # Find a "-" that's not at position 0
        range_separator_pos = part.find("-", 1)
        if range_separator_pos != -1:
            # Handle range (e.g., "5-7" or "-5-7")
            # Split only at the found separator position
            start_str = part[:range_separator_pos]
            end_str = part[range_separator_pos + 1:]

            try:
                start = int(start_str.strip())
                end = int(end_str.strip())
            except ValueError as e:
                raise ValueError(
                    _("Invalid range: {value}").format(value=part)
                ) from e

            if start <= 0 or end <= 0:
                raise ValueError(
                    _("{items} must be positive: {value}").format(
                        items=item_name_plural,
                        value=part,
                    )
                )

            if start > end:
                raise ValueError(
                    _("Range start must be <= end: {value}").format(value=part)
                )

            selected_ids.update(range(start, end + 1))
        else:
            # Handle single ID (including negative numbers which we'll reject)
            try:
                selected_id = int(part)
            except ValueError as e:
                raise ValueError(
                    _("Invalid {item}: {value}").format(
                        item=invalid_item_name,
                        value=part,
                    )
                ) from e

            if selected_id <= 0:
                raise ValueError(
                    _("{item} must be positive: {value}").format(
                        item=item_name,
                        value=part,
                    )
                )

            selected_ids.add(selected_id)

    return sorted(selected_ids)


def parse_line_selection(selection: str) -> list[int]:
    """Parse a line selection string into a list of line IDs."""
    return parse_positive_selection(selection)


def parse_line_selection_ranges(selection: str) -> LineRanges:
    """Parse a line selection string into normalized line ranges."""
    if not selection or not selection.strip():
        raise ValueError(_("Selection string cannot be empty"))
    return LineRanges.from_ranges(scan_line_range_specs((selection,)))


def format_line_ids(line_ids: Iterable[str | int]) -> str:
    """Format a list of line IDs into a compact range representation.

    Converts consecutive line IDs into ranges for compact display.
    Examples:
    - [1, 2, 3] → "1-3"
    - [1, 3, 5] → "1,3,5"
    - [1, 2, 3, 5, 7, 8, 9] → "1-3,5,7-9"

    Args:
        line_ids: List of line IDs (as strings or integers)

    Returns:
        Formatted string representation
    """
    if not line_ids:
        return ""

    # Convert to integers, deduplicate, and sort
    ids = sorted(set(int(lid) for lid in line_ids))

    # Group consecutive IDs into ranges
    ranges = []
    start = ids[0]
    end = ids[0]

    for i in range(1, len(ids)):
        if ids[i] == end + 1:
            # Consecutive, extend selected range
            end = ids[i]
        else:
            # Non-consecutive, save selected range and start new one
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = ids[i]
            end = ids[i]

    # Don't forget the last range
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")

    return ",".join(ranges)
