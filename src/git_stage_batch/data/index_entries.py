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


@dataclass(frozen=True, slots=True)
class IndexStageEntry:
    """Compact identity for one non-stage-zero index entry."""

    stage: int
    mode: str
    object_id: str


@dataclass(frozen=True, slots=True)
class IndexPathEntries:
    """All index entries relevant to one explicitly scoped path."""

    stage_zero: IndexEntry | None = None
    unmerged_entries: tuple[IndexStageEntry, ...] = ()

    @property
    def is_unmerged(self) -> bool:
        """Return whether the path has any higher-stage index entries."""
        return bool(self.unmerged_entries)


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
    return read_index_path_entries((file_path,))[file_path].stage_zero


def read_index_entries(file_paths: Iterable[str]) -> dict[str, IndexEntry]:
    """Return stage-zero index entries with bounded Git argument batches."""
    return {
        file_path: path_entries.stage_zero
        for file_path, path_entries in read_index_path_entries(file_paths).items()
        if path_entries.stage_zero is not None
    }


def read_index_path_entries(
    file_paths: Iterable[str],
) -> dict[str, IndexPathEntries]:
    """Return all stages for explicitly scoped index paths.

    Missing paths are included with an empty identity.  This makes a caller's
    before/after comparison distinguish absence, a stage-zero entry, and an
    unmerged stage 1/2/3 set without retaining file content in Python memory.
    """
    unique_paths = list(dict.fromkeys(file_paths))
    if not unique_paths:
        return {}

    requested_paths = set(unique_paths)
    stage_zero_entries: dict[str, IndexEntry] = {}
    unmerged_entries: dict[str, list[IndexStageEntry]] = {}
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
            if file_path not in requested_paths or len(parts) < 3:
                continue
            try:
                stage = int(parts[2])
            except ValueError:
                continue
            mode = parts[0].decode("ascii", errors="replace")
            object_id = parts[1].decode("ascii", errors="replace")
            if stage == 0:
                stage_zero_entries[file_path] = IndexEntry(mode, object_id)
            else:
                unmerged_entries.setdefault(file_path, []).append(
                    IndexStageEntry(stage, mode, object_id)
                )
    return {
        file_path: IndexPathEntries(
            stage_zero=stage_zero_entries.get(file_path),
            unmerged_entries=tuple(
                sorted(
                    unmerged_entries.get(file_path, ()),
                    key=lambda entry: entry.stage,
                )
            ),
        )
        for file_path in unique_paths
    }


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
                    "--ignore-submodules=none",
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
                raise CommandError(_("Git returned malformed cached diff output."))
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
        if file_path in requested and visible.get(file_path) != invisible.get(file_path)
    )
