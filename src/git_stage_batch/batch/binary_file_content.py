"""Stored binary file content loading for batch actions."""

from __future__ import annotations

from collections.abc import Mapping

from ..core.buffer import LineBuffer
from ..utils.repository_buffers import load_git_object_as_buffer
from .query import get_batch_commit_sha


def read_binary_file_from_batch(
    batch_name: str,
    file_path: str,
    file_meta: Mapping[str, object],
    *,
    missing_content_message: str | None = None,
) -> LineBuffer | None:
    """Read one binary batch target, or return None for a stored deletion."""
    batch_commit = get_batch_commit_sha(batch_name)
    if not batch_commit:
        raise RuntimeError(f"Batch commit not found for batch '{batch_name}'")

    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        return None

    batch_buffer = load_git_object_as_buffer(f"{batch_commit}:{file_path}")
    if batch_buffer is None:
        if missing_content_message is None:
            missing_content_message = (
                f"Binary file metadata for {file_path} says {change_type}, "
                "but the batch content is missing"
            )
        raise RuntimeError(missing_content_message)
    return batch_buffer
