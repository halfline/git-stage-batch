"""Batch source advancement with refreshed line provenance."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import cast

from ...core.buffer import LineBuffer
from ...core.line_selection import LineRanges, coerce_line_ranges
from ...core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    FileSnapshot,
    WorktreeSpace,
    content_snapshot,
    require_same_snapshot,
)
from ...git_paths import display_path
from ...i18n import _
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from .snapshots import create_batch_source_commit
from ...utils.repository_buffers import (
    read_git_object_buffer_or_empty,
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...utils.git_repository import get_git_repository_root_path
from ..line_matching.comparison import (
    SemanticChangeKind,
    SemanticChangeRun,
    stream_semantic_change_runs,
)
from ..line_matching.lineage import (
    BatchSourceLineage,
    LineageRun,
    SourceSelectionExpansion,
)
from ..line_matching.transforms import BatchSourceExactTransform
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.sequence_equality import line_slice_equals
from ..merge.baseline_replacement_ranges import collect_replacement_source_ranges
from ..ownership.model import BatchOwnership
from ..file_state import (
    BatchFileState,
    BatchMetadataRevision,
    SourceBoundOwnership,
)
from ..ownership.remapping import remap_batch_ownership_with_transform
from ..state.validation import get_validated_baseline_commit
from ..state.query import read_batch_metadata


_SOURCE_RANGE_RECORD_FORMAT = "QQ"


class BatchSourceAdvanceError(ValueError):
    """Expected refusal while reconciling a stale batch source."""


@dataclass
class SourceContentWithLineProvenance:
    """Synthesized source buffer with line provenance from its inputs."""

    source_buffer: LineBuffer
    lineage: BatchSourceLineage

    def close(self) -> None:
        """Release the synthesized buffer and line lineage."""
        try:
            self.source_buffer.close()
        finally:
            self.lineage.close()

    def __enter__(self) -> SourceContentWithLineProvenance:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@dataclass
class BatchSourceAdvanceResult:
    """Result of advancing one file's batch source."""

    batch_source_commit: str
    ownership: BatchOwnership
    source_buffer: LineBuffer
    lineage: BatchSourceLineage
    source_transform: BatchSourceExactTransform[
        BatchSourceSpace,
        BatchSourceSpace,
    ]
    working_transform: BatchSourceExactTransform[
        WorktreeSpace,
        BatchSourceSpace,
    ]

    def close(self) -> None:
        """Release the refreshed source buffer and line lineage."""
        try:
            self.source_buffer.close()
        finally:
            self.lineage.close()

    def __enter__(self) -> BatchSourceAdvanceResult:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@dataclass
class AdvancedBatchFileState:
    """Owned result of atomically advancing one source-bound batch file."""

    file_state: BatchFileState
    lineage: BatchSourceLineage
    source_commit: str
    source_transform: BatchSourceExactTransform[
        BatchSourceSpace,
        BatchSourceSpace,
    ]
    working_transform: BatchSourceExactTransform[
        WorktreeSpace,
        BatchSourceSpace,
    ]

    def close(self) -> None:
        """Release the advanced source buffer and exact lineage."""
        try:
            close = getattr(self.file_state.source_lines, "close", None)
            if close is not None:
                close()
        finally:
            self.lineage.close()

    def __enter__(self) -> AdvancedBatchFileState:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def advance_batch_file_state(
    batch_file: BatchFileState,
    *,
    working_snapshot: FileSnapshot[WorktreeSpace],
    working_lines: Sequence[bytes],
) -> AdvancedBatchFileState:
    """Advance source, ownership, and identity as one validated aggregate."""
    if batch_file.path != working_snapshot.path:
        raise ValueError("working snapshot path does not match batch file")
    batch_file.validate()
    require_same_snapshot(
        working_snapshot,
        content_snapshot(batch_file.path, working_lines, space=WorktreeSpace),
    )
    source_with_provenance = advance_source_lines_preserving_existing_presence(
        old_lines=batch_file.source_lines,
        working_lines=working_lines,
        ownership=batch_file.ownership,
    )
    try:
        new_source_commit = create_batch_source_commit(
            batch_file.path,
            file_buffer_override=source_with_provenance.source_buffer,
        )
        new_source_snapshot = content_snapshot(
            batch_file.path,
            source_with_provenance.source_buffer,
            space=BatchSourceSpace,
        )
        source_transform = BatchSourceExactTransform.from_source_lineage(
            batch_file.source_snapshot,
            new_source_snapshot,
            source_with_provenance.lineage,
        )
        working_transform = BatchSourceExactTransform.from_working_lineage(
            working_snapshot,
            new_source_snapshot,
            source_with_provenance.lineage,
        )
        remapped_ownership = remap_batch_ownership_with_transform(
            ownership=batch_file.ownership,
            transform=source_transform,
        )
        advanced = batch_file.with_advanced_source(
            source_snapshot=new_source_snapshot,
            source_lines=source_with_provenance.source_buffer,
            bound_ownership=SourceBoundOwnership(
                new_source_snapshot,
                remapped_ownership,
            ),
            metadata_revision=batch_file.metadata_revision,
        )
        return AdvancedBatchFileState(
            advanced,
            source_with_provenance.lineage,
            new_source_commit,
            source_transform,
            working_transform,
        )
    except BaseException:
        source_with_provenance.close()
        raise


def advance_source_lines_preserving_existing_presence(
    old_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> SourceContentWithLineProvenance:
    """Reconcile source content with the worktree and retain owned provenance.

    Owned source lines in a changed semantic run are advanced to the live
    variant when the complete source side is owned.  Partial ownership remains
    conservative: required old lines are retained beside the live variant so
    unrelated ownership is never silently absorbed.  Source-only runs that
    contain required lines are retained whole because their unowned lines may
    be shared structural boundaries.  An explicit saved replacement remains
    authoritative when the live run is the baseline variant it suppresses.
    """
    presence_lines = coerce_line_ranges(ownership.presence_line_set())
    lineage = BatchSourceLineage()
    try:
        return SourceContentWithLineProvenance(
            source_buffer=LineBuffer.from_chunks(
                _advanced_source_chunks(
                    old_lines,
                    working_lines,
                    presence_lines,
                    ownership,
                    lineage,
                )
            ),
            lineage=lineage,
        )
    except BaseException:
        lineage.close()
        raise


def _required_source_ranges(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    presence_lines: LineRanges,
) -> MappedRecordVector:
    """Return storage-backed ranges whose old-source lineage is required."""
    required = workspace.record_vector(
        len(presence_lines.ranges()) + len(ownership.deletions),
        _SOURCE_RANGE_RECORD_FORMAT,
    )
    for source_start, source_end in presence_lines.ranges():
        required.append((source_start, source_end))
    for deletion in ownership.deletions:
        if deletion.anchor_line is not None:
            required.append((deletion.anchor_line, deletion.anchor_line))

    if len(required) > 1:
        sort_mapped_records(required)
        _compact_source_ranges(required)
    return required


def _compact_source_ranges(source_ranges: MappedRecordVector) -> None:
    """Coalesce ordered overlapping or adjacent mapped source ranges."""
    retained_count = 0
    for source_start, source_end in source_ranges:
        if retained_count:
            previous_start, previous_end = source_ranges[retained_count - 1]
            if source_start <= previous_end + 1:
                source_ranges[retained_count - 1] = (
                    previous_start,
                    max(previous_end, source_end),
                )
                continue
        source_ranges[retained_count] = (source_start, source_end)
        retained_count += 1
    source_ranges.truncate(retained_count)


def _required_ranges_in(
    required_ranges: Sequence[tuple[int, ...]],
    source_start: int,
    source_end: int,
) -> Iterator[tuple[int, int]]:
    """Yield required-range intersections with one source interval."""
    low = 0
    high = len(required_ranges)
    while low < high:
        middle = (low + high) // 2
        if required_ranges[middle][1] < source_start:
            low = middle + 1
        else:
            high = middle

    for range_index in range(low, len(required_ranges)):
        required_start, required_end = required_ranges[range_index]
        if required_start > source_end:
            return
        yield max(required_start, source_start), min(required_end, source_end)


def _line_chunks(
    lines: Sequence[bytes],
    start: int,
    end: int,
) -> Iterator[bytes]:
    """Yield one-based inclusive line ranges without materializing a slice."""
    for line_number in range(start, end + 1):
        yield bytes(lines[line_number - 1])


def _advanced_source_chunks(
    old_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_lines: LineRanges,
    ownership: BatchOwnership,
    lineage: BatchSourceLineage,
) -> Iterator[bytes]:
    """Yield reconciled source chunks while recording source and live lineage."""
    source_cursor = 1
    working_cursor = 1
    new_cursor = 1
    workspace = MatcherWorkspace()
    semantic_runs: Iterator[SemanticChangeRun] | None = None

    def emit_unchanged(line_count: int) -> Iterator[bytes]:
        nonlocal source_cursor, working_cursor, new_cursor
        if line_count <= 0:
            return
        source_end = source_cursor + line_count - 1
        working_end = working_cursor + line_count - 1
        lineage.append_source_run(LineageRun(
            old_start=source_cursor,
            old_end=source_end,
            new_start=new_cursor,
        ))
        lineage.append_working_run(LineageRun(
            old_start=working_cursor,
            old_end=working_end,
            new_start=new_cursor,
        ))
        new_cursor += line_count
        yield from _line_chunks(working_lines, working_cursor, working_end)
        source_cursor = source_end + 1
        working_cursor = working_end + 1

    def emit_source(start: int, end: int) -> Iterator[bytes]:
        nonlocal new_cursor
        lineage.append_source_run(LineageRun(
            old_start=start,
            old_end=end,
            new_start=new_cursor,
        ))
        new_cursor += end - start + 1
        yield from _line_chunks(old_lines, start, end)

    def emit_working(
        start: int,
        end: int,
        *,
        source_start: int | None = None,
        source_end: int | None = None,
    ) -> Iterator[bytes]:
        nonlocal new_cursor
        if source_start is not None and source_end is not None:
            lineage.append_source_run(LineageRun(
                old_start=source_start,
                old_end=source_end,
                new_start=new_cursor,
            ))
            source_count = source_end - source_start + 1
            working_count = end - start + 1
            if working_count > source_count:
                lineage.append_source_expansion(SourceSelectionExpansion(
                    source_start=source_start,
                    source_end=source_end,
                    new_start=new_cursor,
                    new_end=new_cursor + working_count - 1,
                ))
        lineage.append_working_run(LineageRun(
            old_start=start,
            old_end=end,
            new_start=new_cursor,
        ))
        new_cursor += end - start + 1
        yield from _line_chunks(working_lines, start, end)

    try:
        required_source_ranges = _required_source_ranges(
            workspace,
            ownership,
            presence_lines,
        )
        semantic_runs = stream_semantic_change_runs(old_lines, working_lines)
        for run in semantic_runs:
            unchanged_count = _unchanged_line_count_before_run(
                run,
                source_cursor=source_cursor,
                working_cursor=working_cursor,
            )
            yield from emit_unchanged(unchanged_count)

            if run.kind is SemanticChangeKind.PRESENCE:
                assert run.target_start is not None
                assert run.target_end is not None
                yield from emit_working(run.target_start, run.target_end)
                working_cursor = run.target_end + 1
                continue

            assert run.source_start is not None
            assert run.source_end is not None
            if run.kind is SemanticChangeKind.DELETION:
                if next(_required_ranges_in(
                    required_source_ranges,
                    run.source_start,
                    run.source_end,
                ), None) is not None:
                    yield from emit_source(run.source_start, run.source_end)
                source_cursor = run.source_end + 1
                continue

            assert run.kind is SemanticChangeKind.REPLACEMENT
            assert run.target_start is not None
            assert run.target_end is not None
            source_count = run.source_end - run.source_start + 1
            target_count = run.target_end - run.target_start + 1
            fully_owned = (
                presence_lines.count(run.source_start, run.source_end)
                == source_count
            )

            saved_replacement_spans = _saved_replacement_target_spans(
                run,
                working_lines,
                ownership,
                workspace,
            )
            try:
                if saved_replacement_spans:
                    target_cursor = run.target_start
                    source_emitted = False
                    for span_start, span_end in saved_replacement_spans:
                        if target_cursor < span_start:
                            yield from emit_working(
                                target_cursor,
                                span_start - 1,
                            )
                        if not source_emitted:
                            yield from emit_source(run.source_start, run.source_end)
                            source_emitted = True
                        target_cursor = span_end + 1
                    if target_cursor <= run.target_end:
                        yield from emit_working(
                            target_cursor,
                            run.target_end,
                        )
                elif fully_owned:
                    if source_count > target_count:
                        raise BatchSourceAdvanceError(
                            _(
                                "Cannot advance a fully owned source replacement that "
                                "contracts multiple owned lines: source lineage would "
                                "not be unique."
                            )
                        )
                    yield from emit_working(
                        run.target_start,
                        run.target_end,
                        source_start=run.source_start,
                        source_end=run.source_end,
                    )
                else:
                    for required_start, required_end in (
                        _required_ranges_in(
                            required_source_ranges,
                            run.source_start,
                            run.source_end,
                        )
                    ):
                        yield from emit_source(required_start, required_end)
                    yield from emit_working(run.target_start, run.target_end)
            finally:
                workspace.close_resource(saved_replacement_spans)

            source_cursor = run.source_end + 1
            working_cursor = run.target_end + 1

        source_remaining = len(old_lines) - source_cursor + 1
        working_remaining = len(working_lines) - working_cursor + 1
        if source_remaining != working_remaining:
            raise ValueError("Semantic source comparison left unequal trailing spans")
        yield from emit_unchanged(source_remaining)
    finally:
        try:
            if semantic_runs is not None:
                close = getattr(semantic_runs, "close", None)
                if close is not None:
                    close()
        finally:
            workspace.close()


def _unchanged_line_count_before_run(
    run: SemanticChangeRun,
    *,
    source_cursor: int,
    working_cursor: int,
) -> int:
    """Return the matched span immediately preceding one semantic run."""
    if run.source_start is not None:
        source_count = run.source_start - source_cursor
    else:
        source_count = None
    if run.target_start is not None:
        working_count = run.target_start - working_cursor
    else:
        working_count = None

    if source_count is not None and working_count is not None:
        if source_count != working_count:
            raise ValueError("Semantic source comparison produced unequal matched spans")
        return source_count
    if source_count is not None:
        return source_count
    if working_count is not None:
        return working_count
    raise ValueError("Semantic change run has no source or target range")


def _saved_replacement_target_spans(
    run: SemanticChangeRun,
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    workspace: MatcherWorkspace,
) -> MappedRecordVector:
    """Return unique live subranges suppressed by a saved replacement.

    A semantic matcher may group the suppressed baseline variant with adjacent
    live-only edits.  Locate the exact suppressed subrange so those neighboring
    edits remain in place while the authoritative saved replacement is kept.
    Refuse ambiguous baseline occurrences.  If no suppressed baseline text is
    present, the fully owned live run is a newer replacement revision and the
    ordinary advancement path may adopt it.
    """
    assert run.source_start is not None
    assert run.source_end is not None
    assert run.target_start is not None
    assert run.target_end is not None
    source_count = run.source_end - run.source_start + 1
    matched_unit_spans: MappedRecordVector | None = None

    for unit in ownership.replacement_units:
        replacement_ranges = collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        if replacement_ranges is None:
            continue
        try:
            covered_source_count = sum(
                min(source_end, run.source_end)
                - max(source_start, run.source_start)
                + 1
                for source_start, source_end in replacement_ranges
                if (
                    source_start <= run.source_end
                    and source_end >= run.source_start
                )
            )
        finally:
            workspace.close_resource(replacement_ranges)
        if covered_source_count != source_count:
            continue

        unit_spans = workspace.record_vector(
            len(unit.deletion_indices),
            _SOURCE_RANGE_RECORD_FORMAT,
        )
        try:
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is not int
                    or deletion_index < 0
                    or deletion_index >= len(ownership.deletions)
                ):
                    continue
                suppressed_variant = ownership.deletions[
                    deletion_index
                ].content_lines
                variant_count = len(suppressed_variant)
                if variant_count == 0:
                    continue
                first_target_index = run.target_start - 1
                final_target_index = run.target_end - variant_count
                matched_start: int | None = None
                for target_index in range(
                    first_target_index,
                    final_target_index + 1,
                ):
                    if not line_slice_equals(
                        working_lines,
                        target_index,
                        suppressed_variant,
                    ):
                        continue
                    if matched_start is not None:
                        raise BatchSourceAdvanceError(
                            _(
                                "Cannot advance an authoritative replacement with "
                                "multiple matching live baseline spans."
                            )
                        )
                    matched_start = target_index + 1
                if matched_start is not None:
                    unit_spans.append((
                        matched_start,
                        matched_start + variant_count - 1,
                    ))

            if len(unit_spans) > 1:
                sort_mapped_records(unit_spans)
                previous_end = unit_spans[0][1]
                for span_index in range(1, len(unit_spans)):
                    span_start, span_end = unit_spans[span_index]
                    if span_start <= previous_end:
                        raise BatchSourceAdvanceError(
                            _(
                                "Cannot advance an authoritative replacement with "
                                "overlapping live baseline spans."
                            )
                        )
                    previous_end = span_end
            if not unit_spans:
                continue
            if matched_unit_spans is None:
                matched_unit_spans = workspace.record_vector(
                    len(unit_spans),
                    _SOURCE_RANGE_RECORD_FORMAT,
                )
                for span in unit_spans:
                    matched_unit_spans.append(span)
            elif (
                len(matched_unit_spans) != len(unit_spans)
                or any(
                    matched_unit_spans[index] != unit_spans[index]
                    for index in range(len(unit_spans))
                )
            ):
                raise BatchSourceAdvanceError(
                    _(
                        "Cannot advance an authoritative replacement with multiple "
                        "matching live baseline spans."
                    )
                )
        finally:
            workspace.close_resource(unit_spans)

    if matched_unit_spans is None:
        return workspace.record_vector(0, _SOURCE_RANGE_RECORD_FORMAT)
    return matched_unit_spans


def advance_batch_source_for_file_with_provenance(
    batch_name: str,
    file_path: str,
    old_batch_source_commit: str,
    existing_ownership: BatchOwnership,
) -> BatchSourceAdvanceResult:
    """Advance batch source and expose provenance for re-annotation."""
    repo_root = get_git_repository_root_path()
    working_file_path = repo_root / file_path
    if not os.path.lexists(working_file_path):
        raise ValueError(
            _(
                "Cannot advance batch source for {file}: "
                "file does not exist in working tree"
            ).format(file=display_path(file_path))
        )

    baseline_commit = get_validated_baseline_commit(batch_name)
    metadata = read_batch_metadata(batch_name)
    metadata_revision = metadata.get("revision")
    if not isinstance(metadata_revision, str) or not metadata_revision:
        raise ValueError("batch metadata has no durable revision")

    old_source_buffer = read_git_object_buffer_or_none(
        f"{old_batch_source_commit}:{file_path}"
    )
    if old_source_buffer is None:
        raise ValueError(
            _("Cannot read old batch source for {file} at {commit}").format(
                file=display_path(file_path),
                commit=old_batch_source_commit,
            )
        )

    with (
        old_source_buffer as old_source_lines,
        read_git_object_buffer_or_empty(
            f"{baseline_commit}:{file_path}"
        ) as baseline_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        baseline_snapshot = content_snapshot(
            file_path,
            baseline_lines,
            space=BaselineSpace,
        )
        source_snapshot = content_snapshot(
            file_path,
            old_source_lines,
            space=BatchSourceSpace,
        )
        batch_file = BatchFileState(
            path=file_path,
            baseline_snapshot=baseline_snapshot,
            source_snapshot=source_snapshot,
            baseline_lines=baseline_lines,
            source_lines=old_source_lines,
            bound_ownership=SourceBoundOwnership(
                source_snapshot,
                existing_ownership,
            ),
            metadata_revision=BatchMetadataRevision(metadata_revision),
        )
        advanced = advance_batch_file_state(
            batch_file,
            working_snapshot=content_snapshot(
                file_path,
                working_lines,
                space=WorktreeSpace,
            ),
            working_lines=working_lines,
        )

    return BatchSourceAdvanceResult(
        batch_source_commit=advanced.source_commit,
        ownership=advanced.file_state.ownership,
        source_buffer=cast(LineBuffer, advanced.file_state.source_lines),
        lineage=advanced.lineage,
        source_transform=advanced.source_transform,
        working_transform=advanced.working_transform,
    )
