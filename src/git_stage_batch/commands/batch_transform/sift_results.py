"""Sift result computation for batch transform commands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ...core.buffer import LineBuffer, buffer_matches
from ...core.models import BinaryFileChange
from ...data.repository_buffers import load_git_object_as_buffer_or_empty


def compute_sifted_binary_file(
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[dict]:
    """Compute a sifted binary file result."""
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = file_meta["change_type"]

    batch_source_buffer = load_git_object_as_buffer_or_empty(
        f"{batch_source_commit}:{file_path}"
    )

    full_path = repo_root / file_path
    working_exists = full_path.exists()
    working_buffer = (
        LineBuffer.from_path(full_path)
        if working_exists else
        LineBuffer.from_bytes(b"")
    )
    target_buffer: LineBuffer | None = None
    try:
        if change_type == "deleted":
            if not working_exists:
                return None
        elif change_type in ("added", "modified"):
            if working_exists and buffer_matches(working_buffer, batch_source_buffer):
                return None
            target_buffer = batch_source_buffer
            batch_source_buffer = None

        old_path = file_path if change_type != "added" else "/dev/null"
        new_path = file_path if change_type != "deleted" else "/dev/null"

        result = {
            "type": "binary",
            "binary_change": BinaryFileChange(
                old_path=old_path,
                new_path=new_path,
                change_type=change_type,
            ),
        }
        if target_buffer is not None:
            result["target_buffer"] = target_buffer
            target_buffer = None
        return result
    finally:
        if batch_source_buffer is not None:
            batch_source_buffer.close()
        working_buffer.close()
        if target_buffer is not None:
            target_buffer.close()
