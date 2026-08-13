"""Git index entry lookups."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
import os

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from ..git_paths import decode_path, nul_records


_PATH_ARGUMENT_BYTE_BUDGET = 64 * 1024
_PATH_ARGUMENT_COUNT_LIMIT = 512


@dataclass(frozen=True)
class IndexEntry:
    """Mode and object id for one index entry."""

    mode: str
    object_id: str


def _path_argument_chunks(file_paths: Sequence[str]) -> Iterator[list[str]]:
    """Yield path arguments well below common process argument limits."""
    chunk: list[str] = []
    chunk_bytes = 0
    for file_path in file_paths:
        path_bytes = len(os.fsencode(file_path)) + 1
        if chunk and (
            len(chunk) >= _PATH_ARGUMENT_COUNT_LIMIT
            or chunk_bytes + path_bytes > _PATH_ARGUMENT_BYTE_BUDGET
        ):
            yield chunk
            chunk = []
            chunk_bytes = 0
        chunk.append(file_path)
        chunk_bytes += path_bytes
    if chunk:
        yield chunk


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
    """Return stage-zero index entries with bounded Git argument batches."""
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return {}

    requested_paths = set(unique_paths)
    entries: dict[str, IndexEntry] = {}
    for path_chunk in _path_argument_chunks(unique_paths):
        result = run_git_command(
            ["ls-files", "--stage", "-z", "--", *path_chunk],
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


def read_intent_to_add_paths(file_paths: Iterable[str]) -> frozenset[str]:
    """Return scoped paths whose cached entry carries intent-to-add.

    Git exposes that flag by changing the cached diff when
    ``--ita-visible-in-index`` is toggled.  Comparing both views avoids
    confusing an intent entry with an ordinarily staged empty blob, whose
    object ID is identical.
    """
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return frozenset()

    def cached_statuses(visibility_option: str) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for path_chunk in _path_argument_chunks(unique_paths):
            result = run_git_command(
                [
                    "diff",
                    "--cached",
                    "--name-status",
                    "-z",
                    "--no-renames",
                    visibility_option,
                    "--",
                    *path_chunk,
                ],
                check=True,
                text_output=False,
                requires_index_lock=False,
                literal_pathspecs=True,
            )
            fields = nul_records(result.stdout)
            if len(fields) % 2 != 0:
                raise CommandError(
                    _("Git returned malformed cached diff output.")
                )
            statuses.update(
                (decode_path(path), status.decode("ascii", errors="replace"))
                for status, path in zip(fields[0::2], fields[1::2])
            )
        return statuses

    visible = cached_statuses("--ita-visible-in-index")
    invisible = cached_statuses("--ita-invisible-in-index")
    requested = set(unique_paths)
    return frozenset(
        file_path
        for file_path in visible.keys() | invisible.keys()
        if file_path in requested
        and visible.get(file_path) != invisible.get(file_path)
    )
