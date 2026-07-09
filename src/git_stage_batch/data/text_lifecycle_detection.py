"""Repository-bound text lifecycle detection."""

from __future__ import annotations

from contextlib import ExitStack

from ..core.buffer import LineBuffer, buffer_byte_count
from ..core.text_lifecycle import TextFileChangeType
from .repository_buffers import load_git_object_as_buffer
from ..utils.git_repository import get_git_repository_root_path


def detect_empty_text_lifecycle_change(
    file_path: str,
    *,
    baseline_ref: str = "HEAD",
) -> TextFileChangeType | None:
    """Return added/deleted for empty text lifecycle diffs with no hunk body."""
    full_path = get_git_repository_root_path() / file_path

    with ExitStack() as stack:
        if full_path.exists() and full_path.is_file():
            working_buffer = stack.enter_context(LineBuffer.from_path(full_path))
            if buffer_byte_count(working_buffer) != 0:
                return None

            baseline_buffer = load_git_object_as_buffer(f"{baseline_ref}:{file_path}")
            if baseline_buffer is None:
                return TextFileChangeType.ADDED
            baseline_buffer.close()
            return None

        baseline_buffer = load_git_object_as_buffer(f"{baseline_ref}:{file_path}")
        if baseline_buffer is not None:
            stack.enter_context(baseline_buffer)
            if buffer_byte_count(baseline_buffer) == 0:
                return TextFileChangeType.DELETED
    return None
