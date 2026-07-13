"""Session-start file buffers for batch source commits."""

from __future__ import annotations

import os
from pathlib import Path

from ...core.buffer import LineBuffer
from ...exceptions import CommandError
from ...i18n import _
from ...utils.file_io import read_file_paths_file, read_text_file_contents
from ...utils.git_object_io import list_git_tree_blobs
from ...utils.git_repository import get_git_repository_root_path
from ...utils.repository_buffers import (
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...utils.paths import (
    get_abort_head_file_path,
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
    get_abort_stash_file_path,
)


def _load_snapshot_as_buffer(snapshot_path: Path) -> LineBuffer:
    if snapshot_path.is_symlink():
        return LineBuffer.from_bytes(os.readlink(os.fsencode(snapshot_path)))
    return LineBuffer.from_path(snapshot_path)


def load_saved_session_file_as_buffer(file_path: str) -> LineBuffer:
    """Load a file buffer as it was at session start.

    For tracked files, extracts from the git stash created by
    initialize_abort_state(). For untracked files, reads from the lazy
    snapshot taken before first modification.

    Args:
        file_path: Repository-relative path to the file

    Returns:
        File buffer, preserving exact encoding and line endings

    Raises:
        CommandError: If the file buffer cannot be retrieved
    """
    # Check if file was untracked and snapshotted
    snapshot_list_path = get_abort_snapshot_list_file_path()
    if snapshot_list_path.exists():
        snapshotted_files = read_file_paths_file(snapshot_list_path)
        if file_path in snapshotted_files:
            # Read from snapshot directory
            snapshot_path = get_abort_snapshots_directory_path() / file_path
            if os.path.lexists(snapshot_path):
                return _load_snapshot_as_buffer(snapshot_path)
            else:
                raise CommandError(
                    _("Snapshot for untracked file not found: {file}").format(file=file_path)
                )

    # File was tracked - extract from stash if it exists, otherwise from baseline
    stash_file_path = get_abort_stash_file_path()
    if stash_file_path.exists():
        stash_commit = read_text_file_contents(stash_file_path).strip()
        if stash_commit:
            # Extract file from stash commit
            # The stash commit contains the working tree state
            buffer = read_git_object_buffer_or_none(f"{stash_commit}:{file_path}")
            if buffer is not None:
                return buffer

    # No stash or file not in stash - file was unchanged at session start
    # Read from baseline (abort HEAD)
    abort_head_path = get_abort_head_file_path()
    if not abort_head_path.exists():
        raise CommandError(_("No session found"))

    baseline_commit = read_text_file_contents(abort_head_path).strip()
    buffer = read_git_object_buffer_or_none(f"{baseline_commit}:{file_path}")
    if buffer is None:
        # File might not exist in baseline (new file)
        return LineBuffer.from_bytes(b"")

    return buffer


def read_session_file_buffers(
    file_paths: list[str],
    *,
    baseline_commit: str,
) -> tuple[dict[str, LineBuffer], set[str]]:
    """Read session-start buffers for several files."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    baseline_blobs = list_git_tree_blobs(baseline_commit, unique_file_paths)
    baseline_existing_files = set(baseline_blobs)

    snapshot_list_path = get_abort_snapshot_list_file_path()
    snapshotted_files = (
        set(read_file_paths_file(snapshot_list_path))
        if snapshot_list_path.exists() else
        set()
    )

    buffers: dict[str, LineBuffer] = {}
    remaining_paths: list[str] = []
    try:
        snapshot_directory = get_abort_snapshots_directory_path()
        for file_path in unique_file_paths:
            if file_path in snapshotted_files:
                snapshot_path = snapshot_directory / file_path
                if os.path.lexists(snapshot_path):
                    buffers[file_path] = _load_snapshot_as_buffer(snapshot_path)
                    continue
                raise CommandError(
                    _("Snapshot for untracked file not found: {file}").format(file=file_path)
                )
            remaining_paths.append(file_path)

        stash_file_path = get_abort_stash_file_path()
        stash_blobs = {}
        if stash_file_path.exists():
            stash_commit = read_text_file_contents(stash_file_path).strip()
            if stash_commit:
                stash_blobs = list_git_tree_blobs(stash_commit, remaining_paths)

        repo_root = get_git_repository_root_path()
        for file_path in remaining_paths:
            stash_blob = stash_blobs.get(file_path)
            if stash_blob is not None:
                buffers[file_path] = load_git_blob_as_buffer(stash_blob.blob_sha)
                continue

            baseline_blob = baseline_blobs.get(file_path)
            if baseline_blob is not None:
                buffers[file_path] = load_git_blob_as_buffer(baseline_blob.blob_sha)
                continue

            file_full_path = repo_root / file_path
            if (
                file_path not in baseline_existing_files
                and os.path.lexists(file_full_path)
            ):
                buffers[file_path] = load_working_tree_file_as_buffer(file_path)
            else:
                buffers[file_path] = LineBuffer.from_bytes(b"")
    except Exception:
        for buffer in buffers.values():
            buffer.close()
        raise

    return buffers, baseline_existing_files
