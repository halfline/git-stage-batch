"""Artifact-backed per-file planning for remaining live text changes."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, TextIO

from ..batch.source.cache import load_session_batch_sources
from ..batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ..batch.state.reference_names import format_batch_state_ref_name
from ..core.diff_parser import acquire_unified_diff
from ..core.hashing import compute_stable_hunk_hash_from_lines
from ..core.models import SingleHunkPatch
from ..utils.file_job_workspace import FileJobWorkspace
from ..utils.file_jobs import OrderedFileJob
from ..utils.git_object_io import resolve_git_objects
from ..utils.git_repository import get_git_repository_root_path
from ..utils.paths import get_context_lines
from ..utils.session_start_point import current_head_commit
from .consumed_selections import load_consumed_selections_metadata
from .live_change_candidates import (
    LiveChangeScanContext,
    prepare_atomic_live_change,
    text_hunk_block_reason,
)
from .live_diff import stream_live_git_diff

@dataclass(frozen=True, slots=True)
class WorktreeStatIdentity:
    """A compact lstat snapshot for one repository worktree path."""

    exists: bool
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class LiveTextFileJob:
    """Compact transport record for one contiguous live text file group."""

    ordinal: int
    file_path: str
    input_manifest_path: str
    hunk_manifest_path: str
    expected_worktree_identity: WorktreeStatIdentity


@dataclass(frozen=True, slots=True)
class LiveChangeCountPlan:
    """One invocation's compact jobs and parent-counted atomic changes."""

    jobs: tuple[OrderedFileJob[LiveTextFileJob], ...]
    atomic_count: int
    repository_root: Path


def capture_worktree_stat_identity(
    file_path: str,
    *,
    repository_root: Path | None = None,
) -> WorktreeStatIdentity:
    """Capture the current lstat identity of one repository-relative path."""
    full_path = (repository_root or Path.cwd()) / file_path
    try:
        metadata = full_path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return WorktreeStatIdentity(
            exists=False,
            mode=0,
            size=0,
            mtime_ns=0,
            ctime_ns=0,
            device=0,
            inode=0,
        )
    return WorktreeStatIdentity(
        exists=True,
        mode=metadata.st_mode,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


@contextmanager
def acquire_live_change_count_plan() -> Iterator[LiveChangeCountPlan]:
    """Build one artifact plan whose workspace outlives execution/reduction."""
    with FileJobWorkspace() as workspace:
        yield _build_live_change_count_plan(workspace)


def _build_live_change_count_plan(
    workspace: FileJobWorkspace,
) -> LiveChangeCountPlan:
    repository_root = get_git_repository_root_path().resolve()
    batch_names = list_batch_names()
    batch_state_commit_by_name = _batch_state_commit_snapshot(batch_names)
    batch_metadata_by_name = read_batch_metadata_for_batches(
        batch_names,
        batch_state_commit_by_name=batch_state_commit_by_name,
    )
    context = LiveChangeScanContext(
        batch_metadata_by_name=batch_metadata_by_name,
    )
    batch_source_by_path = load_session_batch_sources()
    consumed_metadata_by_path = load_consumed_selections_metadata()["files"]
    head_commit = current_head_commit()

    jobs: list[OrderedFileJob[LiveTextFileJob]] = []
    atomic_count = 0
    active_group: _LiveTextFileGroup | None = None
    try:
        with acquire_unified_diff(
            stream_live_git_diff(
                context_lines=get_context_lines(),
                full_index=True,
                ignore_submodules="none",
                submodule_format="short",
            )
        ) as patches:
            for ordinal, item in enumerate(patches):
                if isinstance(item, SingleHunkPatch):
                    grouping_key = (
                        item.path(),
                        item.old_path,
                        item.new_path,
                    )
                    if (
                        active_group is not None
                        and active_group.grouping_key != grouping_key
                    ):
                        jobs.append(active_group.finish())
                        active_group = None

                    stable_hash = compute_stable_hunk_hash_from_lines(
                        item.lines
                    )
                    if (
                        text_hunk_block_reason(
                            item,
                            stable_hash,
                            context,
                        )
                        is not None
                    ):
                        continue

                    if active_group is None:
                        file_path = item.path()
                        file_batch_metadata = _file_batch_metadata(
                            context.metadata_for_path(file_path),
                            file_path,
                        )
                        active_group = _LiveTextFileGroup(
                            workspace=workspace,
                            ordinal=ordinal,
                            file_path=file_path,
                            old_path=item.old_path,
                            new_path=item.new_path,
                            repository_root=repository_root,
                            head_commit=head_commit,
                            batch_source_commit=batch_source_by_path.get(
                                file_path
                            ),
                            batch_metadata_by_name=file_batch_metadata,
                            consumed_file_metadata=consumed_metadata_by_path.get(
                                file_path
                            ),
                            batch_state_commit_by_name={
                                batch_name: batch_state_commit_by_name[
                                    batch_name
                                ]
                                for batch_name in file_batch_metadata
                                if batch_name in batch_state_commit_by_name
                            },
                        )
                    active_group.append_hunk(
                        ordinal,
                        item,
                        stable_hash=stable_hash,
                    )
                    continue

                if active_group is not None:
                    jobs.append(active_group.finish())
                    active_group = None

                candidate, _reason = prepare_atomic_live_change(item, context)
                if candidate is not None:
                    with candidate:
                        atomic_count += 1

            if active_group is not None:
                jobs.append(active_group.finish())
                active_group = None
    finally:
        if active_group is not None:
            active_group.abort()

    return LiveChangeCountPlan(
        jobs=tuple(jobs),
        atomic_count=atomic_count,
        repository_root=repository_root,
    )


class _LiveTextFileGroup:
    """Stream artifacts for one contiguous repository text-file group."""

    def __init__(
        self,
        *,
        workspace: FileJobWorkspace,
        ordinal: int,
        file_path: str,
        old_path: str,
        new_path: str,
        repository_root: Path,
        head_commit: str | None,
        batch_source_commit: str | None,
        batch_metadata_by_name: dict[str, dict],
        consumed_file_metadata: dict | None,
        batch_state_commit_by_name: dict[str, str],
    ) -> None:
        self.workspace = workspace
        self.ordinal = ordinal
        self.file_path = file_path
        self.old_path = old_path
        self.new_path = new_path
        self.head_commit = head_commit
        self.batch_source_commit = batch_source_commit
        self.batch_metadata_by_name = batch_metadata_by_name
        self.consumed_file_metadata = consumed_file_metadata
        self.batch_state_commit_by_name = batch_state_commit_by_name
        self.expected_worktree_identity = capture_worktree_stat_identity(
            file_path,
            repository_root=repository_root,
        )
        self.hunk_manifest_path = workspace.artifact_path(
            ordinal,
            "live-hunks.jsonl",
        )
        self._hunk_manifest: TextIO | None = self.hunk_manifest_path.open(
            "x",
            encoding="utf-8",
        )
        self._patch_artifact_bytes = 0

    @property
    def grouping_key(self) -> tuple[str, str, str]:
        """Return the contiguous grouping identity for this file."""
        return self.file_path, self.old_path, self.new_path

    def append_hunk(
        self,
        ordinal: int,
        item: SingleHunkPatch,
        *,
        stable_hash: str,
    ) -> None:
        """Stream one parser-owned hunk to an artifact and manifest record."""
        if self._hunk_manifest is None:
            raise ValueError("live text file group is closed")
        if (
            item.path(),
            item.old_path,
            item.new_path,
        ) != self.grouping_key:
            raise AssertionError("contiguous text hunk grouping changed paths")

        patch_path = self.workspace.write_buffer(
            self.ordinal,
            "live-hunk.patch",
            item.lines,
        )
        self._patch_artifact_bytes += patch_path.stat().st_size
        record = {
            "ordinal": ordinal,
            "old_path": item.old_path,
            "new_path": item.new_path,
            "stable_hash": stable_hash,
            "patch_artifact_path": str(patch_path),
        }
        json.dump(
            record,
            self._hunk_manifest,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        self._hunk_manifest.write("\n")

    def finish(self) -> OrderedFileJob[LiveTextFileJob]:
        """Close manifests and return the compact ordered file job."""
        self._close_hunk_manifest()
        scratch_directory = self.workspace.scratch_directory(self.ordinal)
        input_manifest_path = _write_json_artifact(
            self.workspace,
            self.ordinal,
            "live-input.json",
            {
                "file_path": self.file_path,
                "baseline_path": self.old_path,
                "head_commit": self.head_commit,
                "batch_source_commit": self.batch_source_commit,
                "batch_metadata_by_name": self.batch_metadata_by_name,
                "consumed_file_metadata": self.consumed_file_metadata,
                "batch_state_commit_by_name": (
                    self.batch_state_commit_by_name
                ),
                "scratch_directory": str(scratch_directory),
            },
        )
        estimated_bytes = (
            self.expected_worktree_identity.size
            + self._patch_artifact_bytes
            + self.hunk_manifest_path.stat().st_size
            + input_manifest_path.stat().st_size
        )
        payload = LiveTextFileJob(
            ordinal=self.ordinal,
            file_path=self.file_path,
            input_manifest_path=str(input_manifest_path),
            hunk_manifest_path=str(self.hunk_manifest_path),
            expected_worktree_identity=self.expected_worktree_identity,
        )
        return OrderedFileJob(
            ordinal=self.ordinal,
            file_path=self.file_path,
            estimated_bytes=estimated_bytes,
            payload=payload,
        )

    def abort(self) -> None:
        """Close the manifest writer after an interrupted plan build."""
        self._close_hunk_manifest()

    def _close_hunk_manifest(self) -> None:
        if self._hunk_manifest is None:
            return
        self._hunk_manifest.close()
        self._hunk_manifest = None


def _batch_state_commit_snapshot(
    batch_names: Iterable[str],
) -> dict[str, str]:
    state_ref_by_name = {
        batch_name: format_batch_state_ref_name(batch_name)
        for batch_name in batch_names
    }
    object_info_by_ref = resolve_git_objects(state_ref_by_name.values())
    return {
        batch_name: object_info.object_id
        for batch_name, state_ref in state_ref_by_name.items()
        if (object_info := object_info_by_ref.get(state_ref)) is not None
        and object_info.object_type == "commit"
    }


def _file_batch_metadata(
    batch_metadata_by_name: Mapping[str, dict],
    file_path: str,
) -> dict[str, dict]:
    return {
        batch_name: {
            "files": {
                file_path: metadata["files"][file_path],
            },
        }
        for batch_name, metadata in batch_metadata_by_name.items()
    }


def _write_json_artifact(
    workspace: FileJobWorkspace,
    ordinal: int,
    name: str,
    value: Any,
) -> Path:
    path = workspace.artifact_path(ordinal, name)
    with path.open("x", encoding="utf-8") as output:
        json.dump(
            value,
            output,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        output.write("\n")
    return path
