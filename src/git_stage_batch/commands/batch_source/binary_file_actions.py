"""Binary file actions for batch-source commands."""

from __future__ import annotations

from ...core.buffer import LineBuffer
from ...utils.git import create_git_blob, git_update_index


def stage_binary_file_to_index(
    file_path: str,
    file_meta: dict,
    buffer: LineBuffer | None,
) -> None:
    """Stage one binary batch target into the index."""
    change_type = file_meta.get("change_type", "modified")
    if change_type == "deleted":
        result = git_update_index(file_path=file_path, force_remove=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to stage binary deletion for {file_path}: {result.stderr}"
            )
        return

    if buffer is None:
        raise RuntimeError(f"Binary file not found in batch commit: {file_path}")

    blob_hash = create_git_blob(buffer.byte_chunks())
    file_mode = file_meta.get("mode", "100644")
    git_update_index(file_path=file_path, mode=str(file_mode), blob_sha=blob_hash)
