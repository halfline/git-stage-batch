"""Shared eligibility policy for actionable live repository changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Iterator

from ..batch.state.query import list_batch_names, read_batch_metadata_for_batches
from ..batch.state.metadata_types import BatchMetadataDict
from ..batch.source.annotation import annotate_with_batch_source
from ..core.diff_parser import (
    UnifiedDiffItem,
    acquire_unified_diff,
    build_line_changes_from_patch_lines,
)
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
)
from ..utils.context_lines import get_context_lines
from .change_freshness import text_deletion_change_is_batched
from .applied_batch_overlays import (
    AppliedBatchOverlaySnapshot,
    AppliedBatchOverlayView,
    fresh_applied_batch_overlay_for_path,
    load_applied_batch_overlay_snapshot,
)
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
    raw_patch: UnifiedDiffItem

    def close(self) -> None:
        """Close raw patch storage owned by this prepared candidate."""
        if isinstance(self.raw_patch, SingleHunkPatch) and isinstance(
            self.raw_patch.lines,
            LineBuffer,
        ):
            self.raw_patch.lines.close()

    def __enter__(self) -> EligibleLiveChange:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class LiveChangeScanContext:
    """Repository policy state loaded at most once for one diff scan."""

    def __init__(
        self,
        *,
        batch_metadata_by_name: dict[str, BatchMetadataDict] | None = None,
    ) -> None:
        self.blocked_paths = read_file_paths_file(get_blocked_files_file_path())
        self.blocked_hashes = read_text_file_line_set(get_block_list_file_path())
        self._metadata_by_name = batch_metadata_by_name
        self._metadata_by_path: (
            dict[str, dict[str, BatchMetadataDict]] | None
        ) = None
        self._applied_overlay_by_path: dict[str, AppliedBatchOverlayView] = {}
        self._applied_overlay_snapshot: AppliedBatchOverlaySnapshot | None = None
        self.saw_skipped_change = False
        self.saw_non_batch_skip = False
        self.masked_batch_names: set[str] = set()

    def metadata_by_name(self) -> dict[str, BatchMetadataDict]:
        """Return the complete batch metadata snapshot for this scan."""
        if self._metadata_by_name is None:
            self._metadata_by_name = read_batch_metadata_for_batches(
                list_batch_names()
            )
        return self._metadata_by_name

    def metadata_for_path(
        self,
        file_path: str,
    ) -> dict[str, BatchMetadataDict]:
        if self._metadata_by_path is None:
            self._metadata_by_path = {}
            for batch_name, metadata in self.metadata_by_name().items():
                for path in metadata.get("files", {}):
                    self._metadata_by_path.setdefault(path, {})[batch_name] = metadata
        return self._metadata_by_path.get(file_path, {})

    def applied_overlay_for_path(self, file_path: str) -> AppliedBatchOverlayView:
        """Return one identity-checked overlay, captured once per scan."""
        if file_path not in self._applied_overlay_by_path:
            if self._applied_overlay_snapshot is None:
                self._applied_overlay_snapshot = load_applied_batch_overlay_snapshot()
            self._applied_overlay_by_path[file_path] = (
                fresh_applied_batch_overlay_for_path(
                    file_path,
                    batch_metadata_by_name=self.metadata_for_path(file_path),
                    snapshot=self._applied_overlay_snapshot,
                )
            )
        return self._applied_overlay_by_path[file_path]

    def record_skip(
        self,
        reason: SkipReason,
        *,
        batch_names: frozenset[str] = frozenset(),
    ) -> None:
        """Record why a parsed live change was ineligible."""
        self.saw_skipped_change = True
        if reason is not SkipReason.ALREADY_BATCHED:
            self.saw_non_batch_skip = True
        self.masked_batch_names.update(batch_names)


def live_change_paths(item: UnifiedDiffItem) -> tuple[str, ...]:
    """Return every repository path covered by one parsed live change."""
    if isinstance(item, RenameChange):
        return item.old_path, item.new_path
    if (
        isinstance(item, FileModeChange)
        and item.index_path is not None
        and item.index_path != item.file_path
    ):
        return item.file_path, item.index_path
    if isinstance(item, SingleHunkPatch) and item.old_path != item.new_path:
        return item.old_path, item.new_path
    return (item.path(),)


def blocked_live_change_reason(
    item: UnifiedDiffItem,
    stable_hash: str,
    context: LiveChangeScanContext,
) -> SkipReason | None:
    """Return the shared blocked hash/path decision for one live change."""
    if stable_hash in context.blocked_hashes:
        return SkipReason.BLOCKED_HASH
    if any(
        is_path_blocked(path, context.blocked_paths)
        for path in live_change_paths(item)
    ):
        return SkipReason.BLOCKED_PATH
    return None


def text_hunk_block_reason(
    item: SingleHunkPatch,
    stable_hash: str,
    context: LiveChangeScanContext,
) -> SkipReason | None:
    """Return the blocked decision for a text hunk and any covering rename."""
    if item.old_path != item.new_path:
        rename_hash = compute_rename_change_hash(
            RenameChange(item.old_path, item.new_path)
        )
        if rename_hash in context.blocked_hashes:
            return SkipReason.BLOCKED_HASH
    return blocked_live_change_reason(item, stable_hash, context)


def prepare_atomic_live_change(
    item: UnifiedDiffItem,
    context: LiveChangeScanContext,
) -> tuple[EligibleLiveChange | None, SkipReason | None]:
    """Apply shared eligibility policy to one non-text live diff item."""
    if isinstance(item, FileModeChange):
        stable_hash = compute_file_mode_change_hash(item)
        change: LiveChange = item
    elif isinstance(item, RenameChange):
        stable_hash = compute_rename_change_hash(item)
        change = item
    elif isinstance(item, TextFileDeletionChange):
        stable_hash = compute_text_file_deletion_hash(item)
        applied_overlay = context.applied_overlay_for_path(item.path())
        if text_deletion_change_is_batched(
            item,
            batch_metadata_by_name=context.metadata_for_path(item.path()),
        ) and "deleted" not in applied_overlay.lifecycle_change_types:
            context.record_skip(
                SkipReason.ALREADY_BATCHED,
                batch_names=frozenset(
                    batch_name
                    for batch_name, metadata in context.metadata_for_path(
                        item.path()
                    ).items()
                    if metadata.get("files", {})
                    .get(item.path(), {})
                    .get("change_type")
                    == "deleted"
                ),
            )
            return None, SkipReason.ALREADY_BATCHED
        change = item
    elif isinstance(item, GitlinkChange):
        stable_hash = compute_gitlink_change_hash(item)
        change = item
    elif isinstance(item, BinaryFileChange):
        change = attach_live_binary_fingerprint(item)
        stable_hash = compute_binary_file_hash(change)
    else:
        raise TypeError(f"Unsupported atomic live diff item: {type(item).__name__}")

    blocked_reason = blocked_live_change_reason(item, stable_hash, context)
    if blocked_reason is not None:
        return None, blocked_reason
    return EligibleLiveChange(change, stable_hash, item), None


def prepare_live_change(
    item: UnifiedDiffItem,
    context: LiveChangeScanContext,
) -> tuple[EligibleLiveChange | None, SkipReason | None]:
    """Apply the common blocked/batched policy to one parsed diff item."""
    if not isinstance(item, SingleHunkPatch):
        return prepare_atomic_live_change(item, context)

    stable_hash = compute_stable_hunk_hash_from_lines(item.lines)
    blocked_reason = text_hunk_block_reason(item, stable_hash, context)
    if blocked_reason is not None:
        return None, blocked_reason

    line_change = build_line_changes_from_patch_lines(
        item.lines,
        annotator=annotate_with_batch_source,
    )
    masked_batch_names: set[str] = set()
    filtered = filter_line_level_change_for_batches(
        line_change,
        batch_metadata_by_name=context.metadata_for_path(line_change.path),
        applied_overlay=context.applied_overlay_for_path(line_change.path),
        masked_batch_names=masked_batch_names,
    )
    if filtered is None:
        context.record_skip(
            SkipReason.ALREADY_BATCHED,
            batch_names=frozenset(masked_batch_names),
        )
        return None, SkipReason.ALREADY_BATCHED

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
    return EligibleLiveChange(
        filtered,
        stable_hash,
        raw_patch,
    ), None


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


@dataclass(frozen=True, slots=True)
class LiveChangeScanResult:
    """First eligible candidate plus complete exhausted-scan diagnostics."""

    candidate: EligibleLiveChange | None
    all_changes_already_batched: bool
    batch_names: frozenset[str]


def next_eligible_live_change_with_summary() -> LiveChangeScanResult:
    """Return one candidate or explain an exhaustion caused only by batches."""
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
            candidate, reason = prepare_live_change(item, context)
            if candidate is not None:
                return LiveChangeScanResult(candidate, False, frozenset())
            if reason is not None and reason is not SkipReason.ALREADY_BATCHED:
                context.record_skip(reason)
    return LiveChangeScanResult(
        candidate=None,
        all_changes_already_batched=(
            context.saw_skipped_change and not context.saw_non_batch_skip
        ),
        batch_names=frozenset(context.masked_batch_names),
    )
