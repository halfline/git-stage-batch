"""Line selection parsing for line-level staging operations."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from ..utils.file_io import read_text_file_contents, write_text_file_contents


def parse_positive_selection(
    selection: str,
    *,
    item_name: str = "Line ID",
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
    if not selection or not selection.strip():
        raise ValueError("Selection string cannot be empty")

    selected_ids = set()
    parts = selection.split(",")
    invalid_item_name = "line ID" if item_name == "Line ID" else item_name

    for part in parts:
        part = part.strip()
        if not part:
            if reject_empty_items:
                raise ValueError("Selection contains an empty item")
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
                raise ValueError(f"Invalid range: {part}") from e

            if start <= 0 or end <= 0:
                raise ValueError(f"{item_name}s must be positive: {part}")

            if start > end:
                raise ValueError(f"Range start must be <= end: {part}")

            selected_ids.update(range(start, end + 1))
        else:
            # Handle single ID (including negative numbers which we'll reject)
            try:
                selected_id = int(part)
            except ValueError as e:
                raise ValueError(f"Invalid {invalid_item_name}: {part}") from e

            if selected_id <= 0:
                raise ValueError(f"{item_name} must be positive: {part}")

            selected_ids.add(selected_id)

    return sorted(selected_ids)


def parse_line_selection(selection: str) -> list[int]:
    """Parse a line selection string into a list of line IDs."""
    return parse_positive_selection(selection, item_name="Line ID")


def read_line_ids_file(path: Path) -> list[int]:
    """Read a file containing line IDs (one per line) and return as a list."""
    if not path.exists():
        return []

    ids: list[int] = []
    for line in read_text_file_contents(path).splitlines():
        value = line.strip()
        if value.isdigit():
            ids.append(int(value))
    return ids


def write_line_ids_file(path: Path, ids: Iterable[int]) -> None:
    """Write line IDs to a file (one per line), sorted and deduplicated."""
    unique_sorted_ids = sorted(set(ids))
    write_text_file_contents(path, "\n".join(str(i) for i in unique_sorted_ids) + ("\n" if unique_sorted_ids else ""))


def format_line_ids(line_ids: list[str | int]) -> str:
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
