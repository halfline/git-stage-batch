"""Line selection parsing for line-level staging operations."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .state import read_text_file_contents, write_text_file_contents


def parse_line_selection(selection: str) -> list[int]:
    """Parse a line selection string into a list of line IDs.

    Supports:
    - Individual IDs: "1,2,3" → [1, 2, 3]
    - Ranges: "5-7" → [5, 6, 7]
    - Mixed: "1,3,5-7" → [1, 3, 5, 6, 7]

    Args:
        selection: Comma-separated line IDs and/or ranges (e.g., "1,3,5-7")

    Returns:
        Sorted list of unique line IDs

    Raises:
        ValueError: If the selection string is invalid
    """
    if not selection or not selection.strip():
        raise ValueError("Selection string cannot be empty")

    line_ids = set()
    parts = selection.split(",")

    for part in parts:
        part = part.strip()
        if not part:
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
                raise ValueError(f"Line IDs must be positive: {part}")

            if start > end:
                raise ValueError(f"Range start must be <= end: {part}")

            line_ids.update(range(start, end + 1))
        else:
            # Handle single ID (including negative numbers which we'll reject)
            try:
                line_id = int(part)
            except ValueError as e:
                raise ValueError(f"Invalid line ID: {part}") from e

            if line_id <= 0:
                raise ValueError(f"Line ID must be positive: {part}")

            line_ids.add(line_id)

    return sorted(line_ids)


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
