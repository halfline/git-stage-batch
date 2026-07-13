"""Generic batch file entry storage operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Optional

from ..utils.repository_buffers import read_git_object_buffer_or_none
from ..utils.git_command import run_git_command
from ..utils.git_object_io import create_git_blob
from .state import content_commits as _content_commits
from .state.query import get_batch_commit_sha, read_batch_metadata
from .state.compatibility_metadata import write_file_backed_batch_metadata
from .state.batch_names import validate_batch_name


def remove_file_from_batch(batch_name: str, file_path: str) -> None:
    """Remove a file from batch metadata and batch commit tree."""
    metadata = read_batch_metadata(batch_name)
    metadata.get("files", {}).pop(file_path, None)
    write_file_backed_batch_metadata(batch_name, metadata)
    _content_commits.remove_file_from_batch_commit(batch_name, file_path)


def copy_file_from_batch_to_batch(
    source_batch: str,
    dest_batch: str,
    file_path: str,
) -> None:
    """Copy one batch file's metadata and realized content into another batch."""
    source_metadata = read_batch_metadata(source_batch)
    file_meta = source_metadata.get("files", {}).get(file_path)
    if file_meta is None:
        raise KeyError(file_path)

    dest_metadata = read_batch_metadata(dest_batch)
    if "files" not in dest_metadata:
        dest_metadata["files"] = {}
    dest_metadata["files"][file_path] = deepcopy(file_meta)

    write_file_backed_batch_metadata(dest_batch, dest_metadata)

    source_commit = get_batch_commit_sha(source_batch)
    if not source_commit:
        _content_commits.remove_file_from_batch_commit(dest_batch, file_path)
        return

    if file_meta.get("file_type") == "gitlink":
        if file_meta.get("change_type") == "deleted":
            _content_commits.remove_file_from_batch_commit(dest_batch, file_path)
            return
        oid = file_meta.get("new_oid")
        if not oid:
            _content_commits.remove_file_from_batch_commit(dest_batch, file_path)
            return
        _content_commits.update_batch_gitlink_commit(dest_batch, file_path, oid)
        return

    source_buffer = read_git_object_buffer_or_none(f"{source_commit}:{file_path}")
    if source_buffer is not None:
        with source_buffer:
            blob_sha = create_git_blob(source_buffer.byte_chunks())
        file_mode = file_meta.get("mode", "100644")
        _content_commits.update_batch_commit(
            dest_batch,
            file_path,
            blob_sha,
            file_mode,
        )
    else:
        _content_commits.remove_file_from_batch_commit(dest_batch, file_path)


def read_file_from_batch(batch_name: str, file_path: str) -> Optional[str]:
    """
    Read a file's content from a batch.

    Returns None if the batch doesn't exist or the file is not in the batch.
    """
    validate_batch_name(batch_name)

    commit_sha = get_batch_commit_sha(batch_name)
    if not commit_sha:
        return None

    result = run_git_command(
        ["show", f"{commit_sha}:{file_path}"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None

    return result.stdout
