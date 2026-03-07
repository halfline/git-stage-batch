"""Parsing and managing line ID selections."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .i18n import _
from .state import exit_with_error, read_text_file_contents, write_text_file_contents


def parse_line_id_specification(specification: str) -> list[int]:
    """
    Parse a line ID specification into a list of integers.

    Supports comma-separated values and ranges:
    - "1,3,5" -> [1, 3, 5]
    - "1-3" -> [1, 2, 3]
    - "1,3,5-7" -> [1, 3, 5, 6, 7]
    """
    if not specification:
        exit_with_error(_("Provide line IDs (e.g. 1,3,5-7)."))

    specification = re.sub(r"\s+", "", specification)
    result: set[int] = set()

    for part in specification.split(","):
        if re.fullmatch(r"\d+-\d+", part):
            start_value, end_value = map(int, part.split("-"))
            if start_value > end_value:
                start_value, end_value = end_value, start_value
            for number in range(start_value, end_value + 1):
                result.add(number)
        elif re.fullmatch(r"\d+", part):
            result.add(int(part))
        else:
            exit_with_error(_("Bad id or range: {}").format(part))

    return sorted(result)


def format_line_ids_as_ranges(ids: list[int]) -> str:
    """
    Format a list of integers as a compact range string.

    Examples:
    - [1, 2, 3, 4, 5] -> "1-5"
    - [1, 3, 5] -> "1,3,5"
    - [1, 2, 3, 5, 7, 8, 9] -> "1-3,5,7-9"
    - [] -> ""
    """
    if not ids:
        return ""

    sorted_ids = sorted(set(ids))
    ranges: list[str] = []
    range_start = sorted_ids[0]
    range_end = sorted_ids[0]

    for i in range(1, len(sorted_ids)):
        if sorted_ids[i] == range_end + 1:
            # Continue current range
            range_end = sorted_ids[i]
        else:
            # End current range and start a new one
            if range_start == range_end:
                ranges.append(str(range_start))
            elif range_end == range_start + 1:
                ranges.append(f"{range_start},{range_end}")
            else:
                ranges.append(f"{range_start}-{range_end}")
            range_start = sorted_ids[i]
            range_end = sorted_ids[i]

    # Add the final range
    if range_start == range_end:
        ranges.append(str(range_start))
    elif range_end == range_start + 1:
        ranges.append(f"{range_start},{range_end}")
    else:
        ranges.append(f"{range_start}-{range_end}")

    return ",".join(ranges)


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
