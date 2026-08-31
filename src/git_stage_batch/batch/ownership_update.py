"""Batch ownership update preparation for selected lines."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import overload

from ..core.coordinates import (
    BatchSourceSpace,
    FileSnapshot,
    WorktreeSpace,
    content_snapshot,
    require_snapshot_role,
)
from ..core.models import LineEntry
from ..core.mapped_storage import MappedRecordVector, sort_mapped_records
from ..core.line_selection import LineRangeBuilder
from .file_state import (
    BatchMetadataRevision,
    SourceBoundOwnership,
)
from .state.metadata_types import BatchFileMetadataDict
from .ownership.hunk_translation import translate_hunk_selection_to_batch_ownership
from .ownership.insertion_references import (
    reanchor_selected_additions_after_relocated_context_boundary,
)
from .ownership.model import BatchOwnership
from .ownership.metadata_loading import acquire_ownership_for_metadata_dict
from .ownership.merging import merge_batch_ownership
from .ownership.translation import translate_lines_to_batch_ownership
from .ownership.replacement_line_runs import ReplacementLineRun
from .ownership.replacement_origins import (
    NoReplacementOrigin,
    ProjectedReplacementOrigin,
    ReplacementOrigin,
    ReplacementOriginSourceProjection,
)
from .ownership.resolved_presence_alternatives import (
    ResolvedPresenceSourceAlternative,
    resolve_presence_source_alternatives,
)
from .merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from .source.refresh import (
    RefreshedBatchSelection,
    ensure_batch_source_current_for_selection,
    prepare_initial_batch_source_for_selection,
)
from .line_matching.match import match_lines
from .line_matching.occurrence_index import normalized_line_payload
from .line_matching.transforms import SameContentSpanProjection, StructuralAlignment
from ..utils.repository_buffers import read_git_object_buffer_or_empty


@dataclass(frozen=True, slots=True)
class SourceBoundLineSelection:
    """Selected rows and the source lines they refer to."""

    source_snapshot: FileSnapshot[BatchSourceSpace]
    lines: Sequence[LineEntry]
    source_alternatives: tuple[ResolvedPresenceSourceAlternative, ...] = ()
    last_nonblank_source_line: int | None = None

    def __post_init__(self) -> None:
        require_snapshot_role(self.source_snapshot, BatchSourceSpace)
        for line in self.lines:
            source_line = line.source_line
            if line.kind == "+" and source_line is None:
                raise ValueError("selected addition has no source coordinate")
            if (
                source_line is not None
                and source_line > self.source_snapshot.line_count
            ):
                raise ValueError("selected line is outside its source snapshot")
        for alternative in self.source_alternatives:
            if alternative.claimed_suffix.end.offset > self.source_snapshot.line_count:
                raise ValueError("selection alternative is outside its source snapshot")
        if (
            self.last_nonblank_source_line is not None
            and not 1
            <= self.last_nonblank_source_line
            <= self.source_snapshot.line_count
        ):
            raise ValueError("last nonblank line is outside the source snapshot")

    def addition_run_uses_source_alternative(
        self,
        *,
        first_new_line: int,
        last_new_line: int,
    ) -> bool:
        """Return whether the rows join two matching source ranges."""
        expected_new_line = first_new_line
        source_lines = LineRangeBuilder()
        for line in self.lines:
            new_line = line.new_line_number
            if line.kind != "+" or new_line is None or new_line < first_new_line:
                continue
            if new_line > last_new_line:
                break
            source_line = line.source_line
            if new_line != expected_new_line or source_line is None:
                return False
            source_lines.add_line(source_line)
            expected_new_line += 1
        if expected_new_line != last_new_line + 1:
            return False
        selected_ranges = source_lines.finish().ranges()
        return any(
            alternative.claimed_ranges == selected_ranges
            for alternative in self.source_alternatives
        )

    def addition_run_has_later_source_lines(
        self,
        *,
        first_new_line: int,
        last_new_line: int,
    ) -> bool:
        """Return whether a current tail came from inside the source file."""
        expected_new_line = first_new_line
        previous_source_line: int | None = None
        for line in self.lines:
            new_line = line.new_line_number
            if line.kind != "+" or new_line is None or new_line < first_new_line:
                continue
            if new_line > last_new_line:
                break
            source_line = line.source_line
            if (
                new_line != expected_new_line
                or source_line is None
                or (
                    previous_source_line is not None
                    and source_line != previous_source_line + 1
                )
            ):
                return False
            expected_new_line += 1
            previous_source_line = source_line
        return (
            expected_new_line == last_new_line + 1
            and previous_source_line is not None
            and self.last_nonblank_source_line is not None
            and previous_source_line < self.last_nonblank_source_line
        )


def _last_nonblank_line(lines: Sequence[bytes]) -> int | None:
    for line_number in range(len(lines), 0, -1):
        if normalized_line_payload(lines[line_number - 1]):
            return line_number
    return None


@dataclass(frozen=True, slots=True)
class PreparedBatchUpdate:
    """Everything needed to store one batch update."""

    batch_source_commit: str
    """Commit that stores the source file."""

    bound_ownership: SourceBoundOwnership
    """Combined ownership for that source file."""

    source_bound_selection: SourceBoundLineSelection
    """Newly selected rows in that source file."""

    expected_metadata_revision: BatchMetadataRevision
    """Batch revision that must still be current when this is stored."""

    def __post_init__(self) -> None:
        if (
            self.bound_ownership.source_snapshot
            != self.source_bound_selection.source_snapshot
        ):
            raise ValueError("prepared selection and ownership use different sources")


class _RefreshedSelectedLineOverlay(Sequence[LineEntry]):
    """Read a full hunk with updated values for its selected rows."""

    def __init__(
        self,
        hunk_lines: Sequence[LineEntry],
        selected_lines: Sequence[LineEntry],
    ) -> None:
        self._hunk_lines = hunk_lines
        self._selected_lines = selected_lines
        self._selected_by_id = MappedRecordVector(len(selected_lines), "QQ")
        try:
            for selected_index, line in enumerate(selected_lines):
                if line.id is not None:
                    self._selected_by_id.append((line.id, selected_index))
            if len(self._selected_by_id) > 1:
                sort_mapped_records(self._selected_by_id)
            previous_id: int | None = None
            for display_id, _selected_index in self._selected_by_id:
                if display_id == previous_id:
                    raise ValueError("refreshed selection has duplicate display IDs")
                previous_id = display_id
        except BaseException:
            self._selected_by_id.close()
            raise

    def __len__(self) -> int:
        return len(self._hunk_lines)

    @overload
    def __getitem__(self, index: int) -> LineEntry: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[LineEntry]: ...

    def __getitem__(self, index: int | slice) -> LineEntry | Sequence[LineEntry]:
        if isinstance(index, slice):
            return _RefreshedSelectedLineOverlaySlice(self, index)
        line = self._hunk_lines[index]
        if line.id is None:
            return line
        selected_index = self._selected_index(line.id)
        return line if selected_index is None else self._selected_lines[selected_index]

    def _selected_index(self, display_id: int) -> int | None:
        low = 0
        high = len(self._selected_by_id)
        while low < high:
            middle = (low + high) // 2
            candidate_id, _selected_index = self._selected_by_id[middle]
            if candidate_id < display_id:
                low = middle + 1
            else:
                high = middle
        if low >= len(self._selected_by_id):
            return None
        candidate_id, selected_index = self._selected_by_id[low]
        return selected_index if candidate_id == display_id else None

    def close(self) -> None:
        self._selected_by_id.close()

    def __enter__(self) -> _RefreshedSelectedLineOverlay:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _RefreshedSelectedLineOverlaySlice(Sequence[LineEntry]):
    """Lazy slice over a refreshed-line overlay."""

    def __init__(
        self,
        parent: _RefreshedSelectedLineOverlay,
        line_slice: slice | range,
    ) -> None:
        self._parent = parent
        self._range = (
            line_slice
            if isinstance(line_slice, range)
            else range(*line_slice.indices(len(parent)))
        )

    def __len__(self) -> int:
        return len(self._range)

    @overload
    def __getitem__(self, index: int) -> LineEntry: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[LineEntry]: ...

    def __getitem__(self, index: int | slice) -> LineEntry | Sequence[LineEntry]:
        if isinstance(index, slice):
            return _RefreshedSelectedLineOverlaySlice(
                self._parent,
                self._range[index],
            )
        return self._parent[self._range[index]]


def _translate_selection_to_batch_ownership(
    selected_lines: list[LineEntry],
    *,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin: ReplacementOrigin = NoReplacementOrigin(),
    baseline_lines: Sequence[bytes] | None = None,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[WorktreeSpace] | None
    ) = None,
) -> BatchOwnership:
    """Translate a selection, using full-hunk replacement context when available."""
    selected_id_builder = LineRangeBuilder()
    for line in selected_lines:
        if line.id is not None:
            selected_id_builder.add_line(line.id)
    selected_ids = selected_id_builder.finish()
    if hunk_lines is not None and replacement_line_runs is not None and selected_ids:
        with _RefreshedSelectedLineOverlay(
            hunk_lines,
            selected_lines,
        ) as refreshed_hunk:
            return translate_hunk_selection_to_batch_ownership(
                refreshed_hunk,
                selected_ids,
                replacement_line_runs=replacement_line_runs,
                replacement_origin=replacement_origin,
                baseline_lines=baseline_lines,
                replacement_origin_source_projection=(
                    replacement_origin_source_projection
                ),
            )

    return translate_lines_to_batch_ownership(selected_lines)


def _include_owned_following_blank(
    selected_lines: list[LineEntry],
    *,
    hunk_lines: Sequence[LineEntry] | None,
    source_lines: Sequence[bytes],
    existing_ownership: BatchOwnership | None,
) -> list[LineEntry]:
    """Include the following selected blank when the block needs it.

    The selection may match an older copy of the block in the source. Without
    its following blank, the batch and worktree would disagree about the space
    before the next block.
    """
    if hunk_lines is None or existing_ownership is None or not selected_lines:
        return selected_lines
    ordered = sorted(
        selected_lines,
        key=lambda line: line.new_line_number or 0,
    )
    if any(
        line.kind != "+"
        or line.id is None
        or line.new_line_number is None
        or line.source_line is None
        for line in ordered
    ):
        return selected_lines
    for previous, current in zip(ordered, ordered[1:]):
        assert previous.new_line_number is not None
        assert previous.source_line is not None
        assert current.new_line_number is not None
        assert current.source_line is not None
        if (
            current.new_line_number != previous.new_line_number + 1
            or current.source_line != previous.source_line + 1
        ):
            return selected_lines

    last = ordered[-1]
    assert last.new_line_number is not None
    assert last.source_line is not None
    blank_new_line = last.new_line_number + 1
    blank_source_line = last.source_line + 1
    if (
        blank_source_line > len(source_lines)
        or blank_source_line not in existing_ownership.presence_line_set()
        or normalized_line_payload(source_lines[blank_source_line - 1])
    ):
        return selected_lines

    selected_ids = {line.id for line in selected_lines}
    blank = next(
        (
            line
            for line in hunk_lines
            if line.kind == "+"
            and line.id is not None
            and line.id not in selected_ids
            and line.new_line_number == blank_new_line
            and not normalized_line_payload(line.text_bytes)
        ),
        None,
    )
    following = next(
        (
            line
            for line in hunk_lines
            if line.kind in {" ", "+"} and line.new_line_number == blank_new_line + 1
        ),
        None,
    )
    if (
        blank is None
        or following is None
        or not normalized_line_payload(following.text_bytes)
    ):
        return selected_lines
    return [*selected_lines, blank.with_source_line(blank_source_line)]


def _ownership_has_baseline_references(ownership: BatchOwnership) -> bool:
    """Return whether newly translated ownership carries baseline coordinates."""
    if any(claim.baseline_references for claim in ownership.presence_claims):
        return True
    if any(deletion.baseline_reference is not None for deletion in ownership.deletions):
        return True
    return any(
        unit.origin is not None and unit.origin.baseline_reference is not None
        for unit in ownership.replacement_units
    )


def _ownership_has_replacement_origin_references(
    ownership: BatchOwnership,
) -> bool:
    """Return whether replacement metadata needs live-HEAD projection."""
    for unit in ownership.replacement_units:
        if unit.origin is None:
            continue
        if unit.origin.baseline_reference is not None:
            return True
        if any(
            type(deletion_index) is int
            and 0 <= deletion_index < len(ownership.deletions)
            and ownership.deletions[deletion_index].baseline_reference is not None
            for deletion_index in unit.deletion_indices
        ):
            return True
    return False


def _prepare_batch_ownership_update_from_refreshed_selection(
    refreshed: RefreshedBatchSelection,
    *,
    batch_source_commit: str,
    source_snapshot: FileSnapshot[BatchSourceSpace],
    source_lines: Sequence[bytes],
    expected_metadata_revision: BatchMetadataRevision,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    reference_source_lines: Sequence[bytes] | None = None,
    reference_target_lines: Sequence[bytes] | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[WorktreeSpace] | None
    ) = None,
) -> PreparedBatchUpdate:
    """Translate and merge a selection whose source is already established."""

    replacement_origin: ReplacementOrigin = NoReplacementOrigin()
    if replacement_origin_line_runs is not None:
        if replacement_origin_source_lines is None:
            raise ValueError("replacement origin runs require source lines")
        replacement_origin = ProjectedReplacementOrigin(
            replacement_origin_line_runs,
            replacement_origin_source_lines,
        )

    prepared_selected_lines = refreshed.selected_lines
    if hunk_lines is not None and replacement_line_runs is not None:
        prepared_selected_lines = (
            reanchor_selected_additions_after_relocated_context_boundary(
                hunk_lines,
                prepared_selected_lines,
                source_lines,
            )
        )
    prepared_selected_lines = _include_owned_following_blank(
        prepared_selected_lines,
        hunk_lines=hunk_lines,
        source_lines=source_lines,
        existing_ownership=refreshed.ownership,
    )

    new_ownership = _translate_selection_to_batch_ownership(
        prepared_selected_lines,
        hunk_lines=hunk_lines,
        replacement_line_runs=replacement_line_runs,
        replacement_origin=replacement_origin,
        baseline_lines=reference_source_lines,
        replacement_origin_source_projection=(replacement_origin_source_projection),
    )
    if (reference_source_lines is None) != (reference_target_lines is None):
        raise ValueError("reference source and target lines must be provided together")
    if replacement_origin_source_lines is not None and reference_target_lines is None:
        raise ValueError(
            "replacement origin source lines require reference target lines"
        )
    if (
        _ownership_has_baseline_references(new_ownership)
        and reference_source_lines is None
    ):
        raise ValueError(
            "selection baseline references require source and batch baseline lines"
        )
    if (
        _ownership_has_replacement_origin_references(new_ownership)
        and replacement_origin_source_lines is None
    ):
        raise ValueError("replacement baseline references require live HEAD lines")
    if reference_source_lines is not None and reference_target_lines is not None:
        translate_ownership_baseline_references(
            new_ownership,
            reference_source_lines,
            reference_target_lines,
            replacement_origin_source_lines=replacement_origin_source_lines,
        )

    if refreshed.ownership:
        merged_ownership = merge_batch_ownership(refreshed.ownership, new_ownership)
    else:
        merged_ownership = new_ownership

    return PreparedBatchUpdate(
        batch_source_commit=batch_source_commit,
        bound_ownership=SourceBoundOwnership(
            source_snapshot,
            merged_ownership,
        ),
        source_bound_selection=SourceBoundLineSelection(
            source_snapshot,
            prepared_selected_lines,
            resolve_presence_source_alternatives(
                new_ownership.presence_line_set(),
                source_lines,
            ),
            _last_nonblank_line(source_lines),
        ),
        expected_metadata_revision=expected_metadata_revision,
    )


@contextmanager
def acquire_batch_ownership_update_for_selection(
    *,
    batch_name: str,
    file_path: str,
    file_metadata: BatchFileMetadataDict | None,
    metadata_revision: BatchMetadataRevision,
    selected_lines: list[LineEntry],
    initial_batch_source_commit: str | None = None,
    hunk_lines: Sequence[LineEntry] | None = None,
    replacement_line_runs: Iterable[ReplacementLineRun] | None = None,
    replacement_origin_line_runs: Iterable[ReplacementLineRun] | None = None,
    reference_source_lines: Sequence[bytes] | None = None,
    batch_baseline_commit: str | None = None,
    replacement_origin_source_lines: Sequence[bytes] | None = None,
    replacement_origin_target_lines: Sequence[bytes] | None = None,
) -> Iterator[PreparedBatchUpdate]:
    """Acquire existing ownership metadata while preparing a batch update.

    The yielded ownership may borrow deletion content from acquired metadata,
    so callers should persist or detach it before leaving the context.
    """
    with ExitStack() as stack:
        current_batch_source_commit: str | None
        if file_metadata is None:
            if initial_batch_source_commit is None:
                current_batch_source_commit, prepared_selected_lines = (
                    prepare_initial_batch_source_for_selection(
                        file_path,
                        selected_lines,
                        coordinate_lines=hunk_lines,
                    )
                )
            else:
                current_batch_source_commit = initial_batch_source_commit
                prepared_selected_lines = selected_lines
            existing_ownership = None
            refreshed = RefreshedBatchSelection(
                batch_source_commit=current_batch_source_commit,
                ownership=existing_ownership,
                selected_lines=prepared_selected_lines,
                source_was_advanced=False,
            )
        else:
            current_batch_source_commit = file_metadata.get("batch_source_commit")
            existing_ownership = stack.enter_context(
                acquire_ownership_for_metadata_dict(file_metadata)
            )
            refreshed = ensure_batch_source_current_for_selection(
                batch_name=batch_name,
                file_path=file_path,
                current_batch_source_commit=current_batch_source_commit,
                existing_ownership=existing_ownership,
                selected_lines=selected_lines,
                coordinate_lines=hunk_lines,
            )

        batch_source_commit = refreshed.batch_source_commit
        if not isinstance(batch_source_commit, str) or not batch_source_commit:
            raise ValueError("selection update has no durable batch source")
        source_lines = stack.enter_context(
            read_git_object_buffer_or_empty(f"{batch_source_commit}:{file_path}")
        )
        source_snapshot = content_snapshot(
            file_path,
            source_lines,
            space=BatchSourceSpace,
        )

        replacement_origin_source_projection: (
            ReplacementOriginSourceProjection[WorktreeSpace] | None
        ) = None
        if replacement_origin_target_lines is not None:
            working_snapshot = content_snapshot(
                file_path,
                replacement_origin_target_lines,
                space=WorktreeSpace,
            )
            if working_snapshot.identity == source_snapshot.identity:
                replacement_origin_source_projection = SameContentSpanProjection(
                    working_snapshot,
                    source_snapshot,
                )
            else:
                replacement_origin_source_projection = stack.enter_context(
                    StructuralAlignment(
                        working_snapshot,
                        source_snapshot,
                        match_lines(
                            source_lines,
                            replacement_origin_target_lines,
                        ).take_reversed(),
                    )
                )

        def prepare_update(
            reference_target_lines: Sequence[bytes] | None,
        ) -> PreparedBatchUpdate:
            return _prepare_batch_ownership_update_from_refreshed_selection(
                refreshed,
                batch_source_commit=batch_source_commit,
                source_snapshot=source_snapshot,
                source_lines=source_lines,
                expected_metadata_revision=metadata_revision,
                hunk_lines=hunk_lines,
                replacement_line_runs=replacement_line_runs,
                replacement_origin_line_runs=replacement_origin_line_runs,
                reference_source_lines=reference_source_lines,
                reference_target_lines=reference_target_lines,
                replacement_origin_source_lines=replacement_origin_source_lines,
                replacement_origin_source_projection=(
                    replacement_origin_source_projection
                ),
            )

        if reference_source_lines is None:
            update = prepare_update(None)
        else:
            if not isinstance(batch_baseline_commit, str) or not (
                batch_baseline_commit
            ):
                raise ValueError(
                    "selection baseline lines require a batch baseline commit"
                )
            with read_git_object_buffer_or_empty(
                f"{batch_baseline_commit}:{file_path}"
            ) as reference_target_lines:
                update = prepare_update(reference_target_lines)

        yield update
