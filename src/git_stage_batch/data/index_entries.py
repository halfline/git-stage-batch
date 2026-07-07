"""Git index entry lookups."""

from __future__ import annotations

from dataclasses import dataclass

from ..utils.git import run_git_command


@dataclass(frozen=True)
class IndexEntry:
    """Mode and object id for one index entry."""

    mode: str
    object_id: str


def read_index_entry(file_path: str) -> IndexEntry | None:
    """Return the exact index entry for a repository path."""
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", file_path],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        if path_bytes.decode("utf-8", errors="surrogateescape") != file_path:
            continue

        parts = metadata.split()
        if len(parts) < 3 or parts[2] != b"0":
            continue
        return IndexEntry(
            mode=parts[0].decode("ascii", errors="replace"),
            object_id=parts[1].decode("ascii", errors="replace"),
        )

    return None
