"""Shared text path lifecycle decisions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from ..editor import buffer_matches
from ..utils.git import get_git_repository_root_path, run_git_command


BufferData = bytes | Sequence[bytes]


class TextFileChangeType(str, Enum):
    """Text path lifecycle states persisted in batch metadata."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


def normalized_text_change_type(change_type: str | TextFileChangeType | None) -> TextFileChangeType:
    """Return a supported text change type, defaulting to modified."""
    if isinstance(change_type, TextFileChangeType):
        return change_type
    try:
        return TextFileChangeType(change_type or TextFileChangeType.MODIFIED.value)
    except ValueError:
        return TextFileChangeType.MODIFIED


def detect_empty_text_lifecycle_change(
    file_path: str,
    *,
    baseline_ref: str = "HEAD",
) -> TextFileChangeType | None:
    """Return added/deleted for empty text lifecycle diffs with no hunk body."""
    full_path = get_git_repository_root_path() / file_path
    baseline_result = run_git_command(
        ["show", f"{baseline_ref}:{file_path}"],
        check=False,
        text_output=False,
    )

    if full_path.exists() and full_path.is_file():
        if full_path.read_bytes() != b"":
            return None
        if baseline_result.returncode != 0:
            return TextFileChangeType.ADDED
        return None

    if baseline_result.returncode == 0 and baseline_result.stdout == b"":
        return TextFileChangeType.DELETED
    return None


def resolve_text_change_type(
    *,
    file_path: str,
    baseline_exists: bool,
    batch_source_content: BufferData,
    realized_content: BufferData,
    requested_change_type: str | TextFileChangeType | None = None,
    working_exists: bool | None = None,
) -> TextFileChangeType:
    """Classify text batches that represent whole-path lifecycle states."""
    requested = (
        None
        if requested_change_type is None else
        normalized_text_change_type(requested_change_type)
    )

    if requested == TextFileChangeType.DELETED:
        return (
            TextFileChangeType.DELETED
            if baseline_exists and _buffer_is_empty(realized_content) else
            TextFileChangeType.MODIFIED
        )

    if requested == TextFileChangeType.ADDED:
        return (
            TextFileChangeType.ADDED
            if (
                not baseline_exists
                and buffer_matches(realized_content, batch_source_content)
            )
            else TextFileChangeType.MODIFIED
        )

    if requested == TextFileChangeType.MODIFIED:
        return TextFileChangeType.MODIFIED

    if (
        not baseline_exists
        and buffer_matches(realized_content, batch_source_content)
    ):
        return TextFileChangeType.ADDED

    if working_exists is None:
        working_exists = (get_git_repository_root_path() / file_path).exists()
    if baseline_exists and not working_exists and _buffer_is_empty(realized_content):
        return TextFileChangeType.DELETED

    return TextFileChangeType.MODIFIED


def _buffer_is_empty(buffer: BufferData) -> bool:
    if isinstance(buffer, bytes):
        return buffer == b""
    return not any(buffer)


def selected_text_target_change_type(
    text_change_type: str | TextFileChangeType,
    selected_ids: Iterable[int] | None,
    target_content: BufferData,
) -> TextFileChangeType:
    """Return the path state for applying selected batch text to a target."""
    text_change_type = normalized_text_change_type(text_change_type)
    if selected_ids is None:
        return text_change_type
    if text_change_type == TextFileChangeType.DELETED and _buffer_is_empty(target_content):
        return TextFileChangeType.DELETED
    return TextFileChangeType.MODIFIED


def selected_text_discard_change_type(
    text_change_type: str | TextFileChangeType,
    selected_ids: Iterable[int] | None,
    discarded_content: BufferData,
    *,
    baseline_exists: bool,
) -> TextFileChangeType:
    """Return the path state for discarding selected batch text from a target."""
    text_change_type = normalized_text_change_type(text_change_type)
    if not baseline_exists and _buffer_is_empty(discarded_content):
        return TextFileChangeType.DELETED
    if selected_ids is None:
        return (
            TextFileChangeType.DELETED
            if text_change_type == TextFileChangeType.ADDED and not baseline_exists else
            TextFileChangeType.MODIFIED
        )
    return TextFileChangeType.MODIFIED


def mode_for_text_materialization(
    file_mode: str | None,
    selected_ids: Iterable[int] | None,
    *,
    destination_exists: bool,
) -> str | None:
    """Return a mode only when writing a whole file or creating a path."""
    if file_mode is None:
        return None
    if selected_ids is None or not destination_exists:
        return str(file_mode)
    return None


def sifted_empty_text_path_change_type(
    change_type: str | TextFileChangeType,
    *,
    target_exists: bool,
    working_exists: bool,
    target_content: bytes,
    ownership_is_empty: bool,
) -> TextFileChangeType:
    """Preserve path-presence-only empty text changes after sift."""
    change_type = normalized_text_change_type(change_type)
    if not ownership_is_empty or target_content != b"":
        return change_type
    if target_exists and not working_exists:
        return TextFileChangeType.ADDED
    if not target_exists and working_exists:
        return TextFileChangeType.DELETED
    return change_type
