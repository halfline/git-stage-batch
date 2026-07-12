"""Batch persistence for atomic executable-mode changes."""

from __future__ import annotations

from ..core.buffer import LineBuffer
from ..core.models import FileModeChange
from ..utils.git_object_io import create_git_blob
from ..utils.git_repository import get_git_repository_root_path
from .state import content_commits as _content_commits
from .state.lifecycle import create_batch
from .state.compatibility_metadata import write_file_backed_batch_metadata
from .state.query import read_batch_metadata
from .source.snapshots import create_batch_source_commit
from .state.batch_names import batch_exists, validate_batch_name


def add_file_mode_to_batch(batch_name: str, change: FileModeChange) -> None:
    """Store a mode action without claiming any file content."""
    validate_batch_name(batch_name)
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_path = change.path()
    full_path = get_git_repository_root_path() / file_path
    with LineBuffer.from_path(full_path) as buffer:
        source_commit = create_batch_source_commit(
            file_path,
            file_buffer_override=buffer,
        )
        blob_sha = create_git_blob(buffer.byte_chunks())

        metadata = read_batch_metadata(batch_name)
        metadata.setdefault("files", {})[file_path] = {
            "file_type": "mode",
            "batch_source_commit": source_commit,
            "old_mode": change.old_mode,
            "new_mode": change.new_mode,
            "mode": change.new_mode,
            "presence_claims": [],
            "deletions": [],
        }
        write_file_backed_batch_metadata(batch_name, metadata)
        _content_commits.update_batch_commit(
            batch_name,
            file_path,
            blob_sha,
            change.new_mode,
            source_buffers={file_path: buffer},
        )
