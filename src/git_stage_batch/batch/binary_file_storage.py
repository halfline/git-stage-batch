"""Binary file batch persistence."""

from __future__ import annotations

import json

from ..core.buffer import LineBuffer
from ..core.models import BinaryFileChange
from ..data.batch_sources import (
    create_batch_source_commit,
    get_batch_source_for_file,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ..utils.file_io import write_text_file_contents
from ..utils.git_object_io import create_git_blob
from ..utils.git_repository import get_git_repository_root_path
from ..utils.paths import get_batch_metadata_file_path
from . import content_commits as _content_commits
from .operations import create_batch
from .query import read_batch_metadata
from .validation import batch_exists, validate_batch_name


def add_binary_file_to_batch(
    batch_name: str,
    binary_change: BinaryFileChange,
    file_mode: str = "100644",
    file_buffer_override: LineBuffer | None = None,
) -> None:
    """Add a binary file change to a batch as an atomic unit."""
    validate_batch_name(batch_name)

    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    file_path = (
        binary_change.new_path
        if binary_change.new_path != "/dev/null" else
        binary_change.old_path
    )

    current_binary_buffer: LineBuffer | None = None
    close_current_binary_buffer = False
    try:
        if binary_change.is_deleted_file():
            batch_source_commit = get_batch_source_for_file(file_path)
            if not batch_source_commit:
                batch_source_commit = create_batch_source_commit(file_path)
                batch_sources = load_session_batch_sources()
                batch_sources[file_path] = batch_source_commit
                save_session_batch_sources(batch_sources)
        else:
            if file_buffer_override is None:
                full_path = get_git_repository_root_path() / file_path
                if not full_path.exists():
                    raise FileNotFoundError(file_path)
                current_binary_buffer = LineBuffer.from_path(full_path)
                close_current_binary_buffer = True
            else:
                current_binary_buffer = file_buffer_override
            batch_source_commit = create_batch_source_commit(
                file_path,
                file_buffer_override=current_binary_buffer,
            )

        if binary_change.is_deleted_file():
            blob_sha = None
        else:
            assert current_binary_buffer is not None
            blob_sha = create_git_blob(current_binary_buffer.byte_chunks())

        metadata = read_batch_metadata(batch_name)
        if "files" not in metadata:
            metadata["files"] = {}

        metadata["files"][file_path] = {
            "file_type": "binary",
            "change_type": binary_change.change_type,
            "batch_source_commit": batch_source_commit,
            "mode": file_mode,
        }

        metadata_path = get_batch_metadata_file_path(batch_name)
        write_text_file_contents(metadata_path, json.dumps(metadata, indent=2))

        source_buffers = (
            {file_path: current_binary_buffer}
            if current_binary_buffer is not None else
            None
        )
        if blob_sha:
            _content_commits.update_batch_commit(
                batch_name,
                file_path,
                blob_sha,
                file_mode,
                source_buffers=source_buffers,
            )
        else:
            _content_commits.remove_file_from_batch_commit(
                batch_name,
                file_path,
                source_buffers=source_buffers,
            )
    finally:
        if close_current_binary_buffer and current_binary_buffer is not None:
            current_binary_buffer.close()
