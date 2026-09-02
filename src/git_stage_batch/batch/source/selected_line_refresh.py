"""Update the source line recorded for each selected diff row."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import overload

from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.models import LineEntry
from ...core.coordinates import BatchSourceSpace, WorktreeSpace
from ..line_matching.lineage import BatchSourceLineage
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..line_matching.sequence_equality import line_slice_equals
from ..line_matching.transforms import BatchSourceExactTransform
from ..line_matching.transforms import EmbeddedContentSpanProjection
from ..line_matching.match import match_lines
from ..ownership.translation import detect_stale_batch_source_for_selection
from .line_coordinates import (
    ExactLineageSourceCoordinates,
    ExactEmbeddedSourceCoordinates,
    ExactTransformSourceCoordinates,
    IdentitySourceCoordinates,
    SourceCoordinateTransform,
    StructuralSourceCoordinates,
    translate_display_source_coordinates,
)


_MAX_UINT64 = (1 << 64) - 1


def _line_entry_content(line: LineEntry) -> bytes:
    return line.text_bytes + (b"\n" if line.has_trailing_newline else b"")


def selected_lines_fit_source(
    selected_lines: list[LineEntry],
    source_lines: Sequence[bytes],
) -> bool:
    """Return whether selected presence lines can be claimed from source bytes."""
    if detect_stale_batch_source_for_selection(selected_lines):
        return False

    for line in selected_lines:
        if line.kind not in (" ", "+"):
            continue
        if line.source_line is None:
            return False

        source_index = line.source_line - 1
        if source_index < 0 or source_index >= len(source_lines):
            return False
        if source_lines[source_index] != _line_entry_content(line):
            return False

    return True


def _selected_line_record_index(
    selected_line_records: Sequence[tuple[int, ...]],
    line_id: int,
) -> int | None:
    """Return the mapped record index for one selected display ID."""
    start = 0
    end = len(selected_line_records)
    while start < end:
        middle = (start + end) // 2
        candidate_id = selected_line_records[middle][0]
        if candidate_id < line_id:
            start = middle + 1
        else:
            end = middle
    if (
        start >= len(selected_line_records)
        or selected_line_records[start][0] != line_id
    ):
        return None
    return start


@contextmanager
def _acquire_coordinate_selected_line_records(
    selected_lines: Sequence[LineEntry],
    coordinate_lines: Sequence[LineEntry] | None,
) -> Iterator[MappedRecordVector | None]:
    """Index selected IDs in mapped storage when complete context identifies them."""
    if coordinate_lines is None:
        yield None
        return

    with MappedRecordVector(
        len(selected_lines),
        "QQQQ",
    ) as selected_line_records:
        for line in selected_lines:
            line_id = line.id
            if type(line_id) is not int or line_id < 0 or line_id > _MAX_UINT64:
                yield None
                return
            selected_line_records.append((line_id, 0, 0, 0))

        sort_mapped_records(selected_line_records)
        previous_line_id: int | None = None
        for line_id, _count, _coordinate_index, _source_line in selected_line_records:
            if line_id == previous_line_id:
                yield None
                return
            previous_line_id = line_id

        for coordinate_index, line in enumerate(coordinate_lines):
            line_id = line.id
            if type(line_id) is not int or line_id < 0 or line_id > _MAX_UINT64:
                continue
            record_index = _selected_line_record_index(
                selected_line_records,
                line_id,
            )
            if record_index is None:
                continue
            selected_id, count, _coordinate_index, source_line = selected_line_records[
                record_index
            ]
            selected_line_records[record_index] = (
                selected_id,
                count + 1,
                coordinate_index + 1,
                source_line,
            )

        if any(record[1] != 1 for record in selected_line_records):
            yield None
            return
        yield selected_line_records


def _refresh_selected_line_coordinates(
    selected_lines: Sequence[LineEntry],
    coordinate_lines: Sequence[LineEntry] | None,
    selected_line_records: MappedRecordVector | None,
    transform: SourceCoordinateTransform,
) -> list[LineEntry]:
    """Refresh selected entries while scanning complete coordinates when usable."""
    lines_to_refresh = (
        coordinate_lines
        if selected_line_records is not None and coordinate_lines is not None
        else selected_lines
    )
    reannotated_lines: list[LineEntry] = []

    for line, source_line in translate_display_source_coordinates(
        lines_to_refresh,
        transform,
    ):
        if selected_line_records is None:
            reannotated_lines.append(line.with_source_line(source_line))
            continue
        line_id = line.id
        if type(line_id) is not int or line_id < 0 or line_id > _MAX_UINT64:
            continue
        record_index = _selected_line_record_index(
            selected_line_records,
            line_id,
        )
        if record_index is None:
            continue
        selected_id, count, coordinate_index, _source_line = selected_line_records[
            record_index
        ]
        selected_line_records[record_index] = (
            selected_id,
            count,
            coordinate_index,
            0 if source_line is None else source_line + 1,
        )

    if selected_line_records is None:
        return reannotated_lines

    assert coordinate_lines is not None
    for selected_line in selected_lines:
        selected_display_id = selected_line.id
        assert selected_display_id is not None
        record_index = _selected_line_record_index(
            selected_line_records,
            selected_display_id,
        )
        assert record_index is not None
        _line_id, _count, coordinate_index, encoded_source_line = selected_line_records[
            record_index
        ]
        reannotated_lines.append(
            coordinate_lines[coordinate_index - 1].with_source_line(
                encoded_source_line - 1 if encoded_source_line > 0 else None
            )
        )

    return reannotated_lines


def _addition_run_end(
    selected_lines: Sequence[LineEntry],
    start_index: int,
) -> int:
    """Return the first index after consecutive added worktree lines."""
    first_new_line = selected_lines[start_index].new_line_number
    if selected_lines[start_index].kind != "+" or first_new_line is None:
        return start_index + 1
    end_index = start_index + 1
    expected_new_line = first_new_line + 1
    while end_index < len(selected_lines):
        line = selected_lines[end_index]
        if line.kind != "+" or line.new_line_number != expected_new_line:
            break
        end_index += 1
        expected_new_line += 1
    return end_index


def _addition_run_matches_worktree(
    selected_lines: Sequence[LineEntry],
    start_index: int,
    end_index: int,
    working_lines: Sequence[bytes],
) -> bool:
    """Return whether a selected run still names its exact worktree text."""
    for line_index in range(start_index, end_index):
        line = selected_lines[line_index]
        new_line = line.new_line_number
        if (
            new_line is None
            or new_line > len(working_lines)
            or _line_entry_content(line) != working_lines[new_line - 1]
        ):
            return False
    return True


def _addition_run_has_contiguous_source(
    selected_lines: Sequence[LineEntry],
    start_index: int,
    end_index: int,
) -> bool:
    """Check whether a selected run already uses consecutive source lines."""
    first_source_line = selected_lines[start_index].source_line
    if first_source_line is None:
        return False
    return all(
        selected_lines[line_index].source_line
        == first_source_line + line_index - start_index
        for line_index in range(start_index, end_index)
    )


def _validated_addition_run_start(
    selected_lines: Sequence[LineEntry],
    start_index: int,
    end_index: int,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    occurrences: LinePayloadOccurrenceIndex,
) -> int | None:
    """Find the one exact source copy of a consecutive selected run."""
    first_new_line = selected_lines[start_index].new_line_number
    assert first_new_line is not None
    run_count = end_index - start_index

    candidate_start: int | None = None
    for run_offset in range(run_count):
        content = working_lines[first_new_line - 1 + run_offset]
        if occurrences.occurrence_count(content) != 1:
            continue
        source_index = next(occurrences.matching_line_indexes(content))
        candidate_start = source_index - run_offset
        break

    if candidate_start is None:
        for run_offset in range(run_count - 1):
            boundary = occurrences.unique_adjacent_boundary_position(
                working_lines[first_new_line - 1 + run_offset],
                working_lines[first_new_line + run_offset],
            )
            if boundary is None:
                continue
            candidate_start = boundary - run_offset - 1
            break

    if (
        candidate_start is None
        or candidate_start < 0
        or candidate_start + run_count > len(source_lines)
        or not line_slice_equals(
            source_lines,
            candidate_start,
            _WorkingLineRun(working_lines, first_new_line - 1, run_count),
        )
    ):
        return None
    return candidate_start


class _WorkingLineRun(Sequence[bytes]):
    """A view of consecutive worktree lines that does not copy them."""

    def __init__(
        self,
        lines: Sequence[bytes],
        start_index: int,
        line_count: int,
    ) -> None:
        self._lines = lines
        self._indexes = range(start_index, start_index + line_count)

    def __len__(self) -> int:
        return len(self._indexes)

    @overload
    def __getitem__(self, index: int) -> bytes: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[bytes]: ...

    def __getitem__(self, index: int | slice) -> bytes | Sequence[bytes]:
        if isinstance(index, slice):
            indexes = self._indexes[index]
            return _WorkingLineRun(
                self._lines,
                indexes.start,
                len(indexes),
            )
        try:
            return self._lines[self._indexes[index]]
        except IndexError as error:
            raise IndexError(index) from error


def _bind_addition_runs_to_unique_source_spans(
    selected_lines: list[LineEntry],
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
) -> list[LineEntry]:
    """Use the one complete source copy when a selected run is split."""
    workspace: MatcherWorkspace | None = None
    occurrences: LinePayloadOccurrenceIndex | None = None
    try:
        start_index = 0
        while start_index < len(selected_lines):
            end_index = _addition_run_end(selected_lines, start_index)
            if not _addition_run_has_contiguous_source(
                selected_lines,
                start_index,
                end_index,
            ) and _addition_run_matches_worktree(
                selected_lines,
                start_index,
                end_index,
                working_lines,
            ):
                if workspace is None:
                    workspace = MatcherWorkspace()
                    occurrences = LinePayloadOccurrenceIndex(
                        workspace,
                        source_lines,
                        normalize_payloads=False,
                    )
                assert occurrences is not None
                source_start = _validated_addition_run_start(
                    selected_lines,
                    start_index,
                    end_index,
                    source_lines,
                    working_lines,
                    occurrences,
                )
                if source_start is not None:
                    for run_offset in range(end_index - start_index):
                        line_index = start_index + run_offset
                        selected_lines[line_index] = selected_lines[
                            line_index
                        ].with_source_line(source_start + run_offset + 1)
            start_index = end_index
    finally:
        if occurrences is not None:
            occurrences.close()
        if workspace is not None:
            workspace.close()
    return selected_lines


def refresh_selected_lines_against_new_source(
    selected_lines: list[LineEntry],
    *,
    coordinate_lines: Sequence[LineEntry] | None = None,
) -> list[LineEntry]:
    """Re-annotate selected lines for a first-time batch source.

    This helper is only used before a batch source exists.
    The initial batch source commit will be created from the same working tree
    snapshot that the selected_lines were derived from, with no transformations
    applied. This means working tree line N in the snapshot maps to batch source
    line N in the new source commit.

    This invariant is maintained by create_batch_source_commit(), which creates
    the first source from the current working tree state. Advanced batch sources
    use refresh_selected_lines_against_source_lines() instead because they
    may preserve already-owned lines that are absent from the working tree.

    For first-time source creation, the mapping is trivial:
    - Context/addition line: working tree line N -> batch source line N
    - Deletion line: uses last known source line as anchor

    Args:
        selected_lines: LineEntry objects with potentially stale source_line values
        coordinate_lines: Complete hunk entries used to locate selected deletions.

    Returns:
        New list of LineEntry objects with refreshed source_line values
    """
    with _acquire_coordinate_selected_line_records(
        selected_lines,
        coordinate_lines,
    ) as selected_line_records:
        return _refresh_selected_line_coordinates(
            selected_lines,
            coordinate_lines,
            selected_line_records,
            IdentitySourceCoordinates(),
        )


def refresh_selected_lines_against_embedded_source(
    selected_lines: list[LineEntry],
    *,
    projection: EmbeddedContentSpanProjection[
        WorktreeSpace,
        BatchSourceSpace,
    ],
    coordinate_lines: Sequence[LineEntry] | None = None,
) -> list[LineEntry]:
    """Update row positions from an exact worktree copy in the source."""
    with _acquire_coordinate_selected_line_records(
        selected_lines,
        coordinate_lines,
    ) as selected_line_records:
        return _refresh_selected_line_coordinates(
            selected_lines,
            coordinate_lines,
            selected_line_records,
            ExactEmbeddedSourceCoordinates(projection),
        )


def refresh_selected_lines_against_source_lines(
    selected_lines: list[LineEntry],
    *,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    lineage: BatchSourceLineage | None = None,
    exact_transforms: tuple[
        BatchSourceExactTransform[BatchSourceSpace, BatchSourceSpace],
        BatchSourceExactTransform[WorktreeSpace, BatchSourceSpace],
    ]
    | None = None,
    coordinate_lines: Sequence[LineEntry] | None = None,
) -> list[LineEntry]:
    """Re-annotate selected lines against source and working-tree line sequences."""
    mapping = None
    if lineage is None and exact_transforms is None:
        mapping = match_lines(source_lines, working_lines)

    try:
        transform: SourceCoordinateTransform
        if exact_transforms is not None:
            transform = ExactTransformSourceCoordinates(*exact_transforms)
        elif lineage is not None:
            transform = ExactLineageSourceCoordinates(lineage)
        else:
            assert mapping is not None
            transform = StructuralSourceCoordinates(mapping)

        with _acquire_coordinate_selected_line_records(
            selected_lines,
            coordinate_lines,
        ) as selected_line_records:
            refreshed_lines = _refresh_selected_line_coordinates(
                selected_lines,
                coordinate_lines,
                selected_line_records,
                transform,
            )
            if lineage is None and exact_transforms is None:
                return _bind_addition_runs_to_unique_source_spans(
                    refreshed_lines,
                    source_lines,
                    working_lines,
                )
            return refreshed_lines
    finally:
        if mapping is not None:
            mapping.close()
