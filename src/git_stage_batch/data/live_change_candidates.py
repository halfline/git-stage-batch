"""Shared eligibility policy for actionable live repository changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from ..batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ..batch.source.annotation import annotate_with_batch_source
from ..core.diff_parser import acquire_unified_diff, build_line_changes_from_patch_lines
from ..core.buffer import LineBuffer
from ..core.hashing import (
    compute_binary_file_hash,
    compute_file_mode_change_hash,
    compute_gitlink_change_hash,
    compute_rename_change_hash,
    compute_stable_hunk_hash_from_lines,
    compute_text_file_deletion_hash,
)
from ..core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    SingleHunkPatch,
    TextFileDeletionChange,
)
from ..utils.file_io import (
    is_path_blocked,
    read_file_paths_file,
    read_text_file_line_set,
)
from ..utils.paths import (
    get_block_list_file_path,
    get_blocked_files_file_path,
    get_context_lines,
)
from .change_freshness import text_deletion_change_is_batched
from .binary_identity import attach_live_binary_fingerprint
from .live_diff import stream_live_git_diff
from .selected_change.hunk_filtering import filter_line_level_change_for_batches


LiveChange = (
    LineLevelChange
    | BinaryFileChange
    | FileModeChange
    | GitlinkChange
    | RenameChange
    | TextFileDeletionChange
)


class SkipReason(Enum):
    """Why a parsed live diff item is not actionable."""

    BLOCKED_HASH = "blocked_hash"
    BLOCKED_PATH = "blocked_path"
    ALREADY_BATCHED = "already_batched"


@dataclass(frozen=True)
class EligibleLiveChange:
    """One prepared actionable change and its raw parsed patch."""

    change: LiveChange
    stable_hash: str
    raw_patch: object

    def close(self) -> None:
        """Close raw patch storage owned by this prepared candidate."""
        if isinstance(self.raw_patch, SingleHunkPatch) and isinstance(
            self.raw_patch.lines,
            LineBuffer,
        ):
            self.raw_patch.lines.close()

    def __enter__(self) -> EligibleLiveChange:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class LiveChangeScanContext:
    """Repository policy state loaded at most once for one diff scan."""

    def __init__(self) -> None:
        self.blocked_paths = read_file_paths_file(get_blocked_files_file_path())
        self.blocked_hashes = read_text_file_line_set(get_block_list_file_path())
        self._metadata_by_name: dict[str, dict] | None = None
        self._metadata_by_path: dict[str, dict[str, dict]] | None = None

    def metadata_for_path(self, file_path: str) -> dict[str, dict]:
        if self._metadata_by_name is None:
            self._metadata_by_name = read_batch_metadata_for_batches(list_batch_names())
        if self._metadata_by_path is None:
            self._metadata_by_path = {}
            for batch_name, metadata in self._metadata_by_name.items():
                for path in metadata.get("files", {}):
                    self._metadata_by_path.setdefault(path, {})[batch_name] = metadata
        return self._metadata_by_path.get(file_path, {})


def _paths_for_item(item: object) -> tuple[str, ...]:
    if isinstance(item, RenameChange):
        return item.old_path, item.new_path
    if isinstance(item, SingleHunkPatch) and item.old_path != item.new_path:
        return item.old_path, item.new_path
    return (item.path(),)  # type: ignore[attr-defined]


def prepare_live_change(
    item: object,
    context: LiveChangeScanContext,
) -> tuple[EligibleLiveChange | None, SkipReason | None]:
    """Apply the common blocked/batched policy to one parsed diff item."""
    if isinstance(item, FileModeChange):
        stable_hash = compute_file_mode_change_hash(item)
        change: LiveChange = item
    elif isinstance(item, RenameChange):
        stable_hash = compute_rename_change_hash(item)
        change = item
    elif isinstance(item, TextFileDeletionChange):
        stable_hash = compute_text_file_deletion_hash(item)
        if text_deletion_change_is_batched(
            item,
            batch_metadata_by_name=context.metadata_for_path(item.path()),
        ):
            return None, SkipReason.ALREADY_BATCHED
        change = item
    elif isinstance(item, GitlinkChange):
        stable_hash = compute_gitlink_change_hash(item)
        change = item
    elif isinstance(item, BinaryFileChange):
        change = attach_live_binary_fingerprint(item)
        stable_hash = compute_binary_file_hash(change)
    elif isinstance(item, SingleHunkPatch):
        if item.old_path != item.new_path:
            rename_hash = compute_rename_change_hash(
                RenameChange(item.old_path, item.new_path)
            )
            if rename_hash in context.blocked_hashes:
                return None, SkipReason.BLOCKED_HASH
        stable_hash = compute_stable_hunk_hash_from_lines(item.lines)
        line_change = build_line_changes_from_patch_lines(
            item.lines,
            annotator=annotate_with_batch_source,
        )
        filtered = filter_line_level_change_for_batches(
            line_change,
            batch_metadata_by_name=context.metadata_for_path(line_change.path),
        )
        if filtered is None:
            return None, SkipReason.ALREADY_BATCHED
        change = filtered
    else:
        raise TypeError(f"Unsupported live diff item: {type(item).__name__}")

    if stable_hash in context.blocked_hashes:
        return None, SkipReason.BLOCKED_HASH
    if any(
        is_path_blocked(path, context.blocked_paths) for path in _paths_for_item(item)
    ):
        return None, SkipReason.BLOCKED_PATH
    if isinstance(item, SingleHunkPatch):
        owned_patch_lines = (
            item.lines.clone()
            if isinstance(item.lines, LineBuffer)
            else LineBuffer.from_chunks(item.lines)
        )
        raw_patch = SingleHunkPatch(
            item.old_path,
            item.new_path,
            owned_patch_lines,
        )
    else:
        raw_patch = item
    return EligibleLiveChange(change, stable_hash, raw_patch), None


def stream_eligible_live_changes() -> Iterator[EligibleLiveChange]:
    """Stream all actionable live changes using one shared policy snapshot."""
    context = LiveChangeScanContext()
    with acquire_unified_diff(
        stream_live_git_diff(
            context_lines=get_context_lines(),
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
        )
    ) as patches:
        for item in patches:
            candidate, _reason = prepare_live_change(item, context)
            if candidate is not None:
                yield candidate


def next_eligible_live_change() -> EligibleLiveChange | None:
    """Return one owned candidate and explicitly close its lazy diff scan."""
    candidates = stream_eligible_live_changes()
    try:
        return next(candidates, None)
    finally:
        close = getattr(candidates, "close", None)
        if close is not None:
            close()
