"""Sift result computation for batch transform commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...batch.line_matching.comparison import (
    SemanticChangeKind,
    derive_semantic_change_runs,
)
from ...batch.merge.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.ownership.absence_content import AbsenceContentBuilder
from ...batch.ownership.model import BatchOwnership
from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.state.query import get_batch_baseline_commit
from ...batch.realized_file_content import build_realized_buffer_from_lines
from ...core.buffer import (
    LineBuffer,
    buffer_byte_count,
    buffer_matches,
)
from ...core.line_selection import LineRanges
from ...core.models import BinaryFileChange, FileModeChange
from ...data.file_modes import detect_file_mode_from_root
from ...core.text_lifecycle import (
    TextFileChangeType,
    normalized_text_change_type,
    sifted_empty_text_path_change_type,
)
from ...utils.repository_buffers import read_git_object_buffer_or_empty
from ...utils.repository_buffers import load_working_tree_file_as_buffer
from ...exceptions import MergeError
from ...core.text_lines import normalize_line_sequence_endings


@dataclass
class SiftedBinaryFileResult:
    """Sift result for a binary file retained in the destination batch."""

    binary_change: BinaryFileChange
    target_buffer: LineBuffer | None = None

    def target_source_buffer(self) -> LineBuffer | None:
        return self.target_buffer

    def close(self) -> None:
        if self.target_buffer is not None:
            self.target_buffer.close()


@dataclass
class SiftedTextFileResult:
    """Sift result for a text file retained in the destination batch."""

    ownership: BatchOwnership
    target_buffer: LineBuffer
    change_type: str
    _closed: bool = field(default=False, init=False, repr=False)

    def target_source_buffer(self) -> LineBuffer | None:
        return self.target_buffer

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _close_text_result_buffers(self.target_buffer, self.ownership)


@dataclass
class SiftedModeFileResult:
    """Retained atomic executable-mode action."""

    mode_change: FileModeChange

    def target_source_buffer(self) -> LineBuffer | None:
        return None

    def close(self) -> None:
        return None


SiftedFileResult = SiftedBinaryFileResult | SiftedTextFileResult | SiftedModeFileResult


def compute_sifted_mode_file(
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> SiftedModeFileResult | None:
    """Drop a mode action once its target mode is already present."""
    change = FileModeChange(file_path, file_meta["old_mode"], file_meta["new_mode"])
    if detect_file_mode_from_root(repo_root, file_path) == change.new_mode:
        return None
    return SiftedModeFileResult(change)


def compute_sifted_binary_file(
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[SiftedBinaryFileResult]:
    """Compute a sifted binary file result."""
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = file_meta["change_type"]

    batch_source_buffer = read_git_object_buffer_or_empty(
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

        result = SiftedBinaryFileResult(
            binary_change=BinaryFileChange(
                old_path=old_path,
                new_path=new_path,
                change_type=change_type,
            ),
        )
        if target_buffer is not None:
            result.target_buffer = target_buffer
            target_buffer = None
        return result
    finally:
        if batch_source_buffer is not None:
            batch_source_buffer.close()
        working_buffer.close()
        if target_buffer is not None:
            target_buffer.close()


def compute_sifted_text_file(
    source_batch: str,
    file_path: str,
    file_meta: dict,
    repo_root: Path,
) -> Optional[SiftedTextFileResult]:
    """Compute a sifted text file result."""
    batch_source_commit = file_meta["batch_source_commit"]
    change_type = normalized_text_change_type(file_meta.get("change_type"))
    baseline_commit = get_batch_baseline_commit(source_batch)
    full_path = repo_root / file_path
    working_exists = full_path.exists()

    batch_source_buffer = read_git_object_buffer_or_empty(
        f"{batch_source_commit}:{file_path}"
    )
    baseline_buffer = (
        read_git_object_buffer_or_empty(f"{baseline_commit}:{file_path}")
        if baseline_commit is not None else
        LineBuffer.from_bytes(b"")
    )
    working_buffer = load_working_tree_file_as_buffer(file_path)
    target_buffer: LineBuffer | None = None

    with (
        batch_source_buffer,
        baseline_buffer,
        working_buffer,
        acquire_ownership_for_metadata_dict(file_meta) as source_ownership,
    ):
        target_buffer = build_realized_buffer_from_lines(
            baseline_buffer,
            batch_source_buffer,
            source_ownership,
        )
        try:
            target_exists = change_type != TextFileChangeType.DELETED
            if target_exists == working_exists and buffer_matches(
                working_buffer,
                target_buffer,
            ):
                return None

            working_lines = normalize_line_sequence_endings(working_buffer)
            target_lines = normalize_line_sequence_endings(target_buffer)

            new_ownership = build_ownership_from_working_and_target_lines(
                working_lines=working_lines,
                target_lines=target_lines,
            )
            if new_ownership is None or new_ownership.is_empty():
                result_change_type = sifted_empty_text_path_change_type(
                    change_type,
                    target_exists=target_exists,
                    working_exists=working_exists,
                    target_content=target_buffer,
                    ownership_is_empty=True,
                )
                if result_change_type == TextFileChangeType.MODIFIED:
                    return None
                new_ownership = BatchOwnership([], [])
            else:
                result_change_type = change_type

            validate_sifted_text_file_result_from_lines(
                target_lines=target_lines,
                dest_ownership=new_ownership,
                working_lines=working_lines,
            )

            returned_target_buffer = target_buffer
            target_buffer = None
            return SiftedTextFileResult(
                ownership=new_ownership,
                target_buffer=returned_target_buffer,
                change_type=result_change_type.value,
            )
        finally:
            if target_buffer is not None:
                target_buffer.close()


def _close_text_result_buffers(
    target_buffer: LineBuffer,
    ownership: BatchOwnership,
) -> None:
    buffers = [target_buffer, *_text_ownership_buffers(ownership)]
    closed_ids: set[int] = set()
    for buffer in buffers:
        buffer_id = id(buffer)
        if buffer_id in closed_ids:
            continue
        closed_ids.add(buffer_id)
        buffer.close()


def _text_ownership_buffers(ownership: BatchOwnership) -> list[LineBuffer]:
    return [
        deletion.content_lines
        for deletion in ownership.deletions
        if isinstance(deletion.content_lines, LineBuffer)
    ]


def build_ownership_from_working_and_target_lines(
    working_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
) -> Optional[BatchOwnership]:
    """Build ownership from normalized working and target byte-line sequences."""
    semantic_runs = derive_semantic_change_runs(
        source_lines=working_lines,
        target_lines=target_lines,
    )

    claimed_ranges: list[tuple[int, int]] = []
    deletion_claims = []

    def build_absence_claim(
        *,
        anchor_line: int | None,
        source_start: int,
        source_end: int,
    ) -> AbsenceClaim:
        with AbsenceContentBuilder() as builder:
            builder.append_line_range(
                working_lines,
                source_start - 1,
                source_end,
            )
            content_lines = builder.finish()
        return AbsenceClaim(
            anchor_line=anchor_line,
            content_lines=content_lines,
        )

    try:
        for run in semantic_runs:
            if run.kind == SemanticChangeKind.PRESENCE:
                if run.target_start is not None and run.target_end is not None:
                    claimed_ranges.append((run.target_start, run.target_end))
            elif run.kind == SemanticChangeKind.DELETION:
                if run.source_start is not None and run.source_end is not None:
                    deletion_claims.append(
                        build_absence_claim(
                            anchor_line=run.target_anchor,
                            source_start=run.source_start,
                            source_end=run.source_end,
                        )
                    )
            elif run.kind == SemanticChangeKind.REPLACEMENT:
                if run.source_start is not None and run.source_end is not None:
                    deletion_claims.append(
                        build_absence_claim(
                            anchor_line=run.target_anchor,
                            source_start=run.source_start,
                            source_end=run.source_end,
                        )
                    )
                if run.target_start is not None and run.target_end is not None:
                    claimed_ranges.append((run.target_start, run.target_end))

        claimed_line_ranges = LineRanges.from_ranges(claimed_ranges)
        if not claimed_line_ranges and not deletion_claims:
            return None

        return BatchOwnership.from_presence_lines(
            claimed_line_ranges.to_range_strings(),
            deletion_claims,
        )
    except BaseException:
        closed_ids: set[int] = set()
        for deletion in deletion_claims:
            content_lines = deletion.content_lines
            if not isinstance(content_lines, LineBuffer):
                continue
            buffer_id = id(content_lines)
            if buffer_id in closed_ids:
                continue
            closed_ids.add(buffer_id)
            content_lines.close()
        raise


def validate_sifted_text_file_result_from_lines(
    target_lines: Sequence[bytes],
    dest_ownership: BatchOwnership,
    working_lines: Sequence[bytes],
) -> None:
    """Validate a sifted representation against normalized byte-line sequences."""
    resolved = dest_ownership.resolve()

    for claimed_line in resolved.presence_line_set:
        if claimed_line < 1 or claimed_line > len(target_lines):
            raise MergeError(
                f"Sift validation failed: claimed line {claimed_line} is out of bounds "
                f"(target content has {len(target_lines)} lines)"
            )

    for deletion_claim in resolved.deletion_claims:
        if deletion_claim.anchor_line is not None:
            if (
                deletion_claim.anchor_line < 1
                or deletion_claim.anchor_line > len(target_lines)
            ):
                raise MergeError(
                    f"Sift validation failed: deletion anchor {deletion_claim.anchor_line} "
                    f"is out of bounds (target content has {len(target_lines)} lines)"
                )
        if not deletion_claim.content_lines:
            raise MergeError("Sift validation failed: absence claim has empty content")

    try:
        reconstructed_buffer = merge_batch_from_line_sequences_as_buffer(
            target_lines,
            dest_ownership,
            working_lines,
        )
    except MergeError as e:
        raise MergeError(
            f"Sift validation failed: destination representation cannot be merged: {e}"
        ) from e

    with reconstructed_buffer as reconstructed:
        if not buffer_matches(reconstructed, target_lines):
            target_byte_count = buffer_byte_count(target_lines)
            raise MergeError(
                f"Sift validation failed: applying destination representation does not produce "
                f"the expected target content. Expected {target_byte_count} bytes, got "
                f"{reconstructed.byte_count} bytes."
            )
