"""Git index entry lookups."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from ..utils.git_command import run_git_command
from ..git_paths import decode_path


@dataclass(frozen=True)
class IndexEntry:
    """Mode and object id for one index entry."""

    mode: str
    object_id: str


def read_index_entry(file_path: str) -> IndexEntry | None:
    """Return the exact index entry for a repository path."""
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", file_path],
        check=True,
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )

    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        if decode_path(path_bytes) != file_path:
            continue

        parts = metadata.split()
        if len(parts) < 3 or parts[2] != b"0":
            continue
        return IndexEntry(
            mode=parts[0].decode("ascii", errors="replace"),
            object_id=parts[1].decode("ascii", errors="replace"),
        )

    return None


def read_index_entries(file_paths: Iterable[str]) -> dict[str, IndexEntry]:
    """Return stage-zero index entries for paths with one Git query."""
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return {}
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", *unique_paths],
        check=True,
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )

    requested_paths = set(unique_paths)
    entries: dict[str, IndexEntry] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        file_path = decode_path(path_bytes)
        parts = metadata.split()
        if (
            file_path not in requested_paths
            or len(parts) < 3
            or parts[2] != b"0"
        ):
            continue
        entries[file_path] = IndexEntry(
            mode=parts[0].decode("ascii", errors="replace"),
            object_id=parts[1].decode("ascii", errors="replace"),
        )
    return entries
