"""Persistence helpers for processed line ID files."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..utils.file_io import read_text_file_contents, write_text_file_contents


def read_line_ids_file(path: Path) -> list[int]:
    """Read a file containing line IDs, one per line."""
    if not path.exists():
        return []

    ids: list[int] = []
    for line in read_text_file_contents(path).splitlines():
        value = line.strip()
        if value.isdigit():
            ids.append(int(value))
    return ids


def write_line_ids_file(path: Path, ids: Iterable[int]) -> None:
    """Write line IDs to a file, sorted and deduplicated."""
    unique_sorted_ids = sorted(set(ids))
    content = "\n".join(str(line_id) for line_id in unique_sorted_ids)
    write_text_file_contents(path, content + ("\n" if unique_sorted_ids else ""))
