"""Selected-line source coordinate refresh helpers."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.models import LineEntry
from ...core.coordinates import BatchSourceSpace, WorktreeSpace
from ..line_matching.lineage import BatchSourceLineage
from ..line_matching.transforms import BatchSourceExactTransform
from ..line_matching.match import match_lines
from ..ownership.translation import detect_stale_batch_source_for_selection
from .line_coordinates import (
    ExactLineageSourceCoordinates,
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
            if (
                type(line_id) is not int
                or line_id < 0
                or line_id > _MAX_UINT64
            ):
                yield None
                return
            selected_line_records.append((line_id, 0, 0, 0))

        sort_mapped_records(selected_line_records)
        previous_line_id: int | None = None
        for line_id, _count, _coordinate_index, _source_line in (
            selected_line_records
        ):
            if line_id == previous_line_id:
                yield None
                return
            previous_line_id = line_id

        for coordinate_index, line in enumerate(coordinate_lines):
            line_id = line.id
            if (
                type(line_id) is not int
                or line_id < 0
                or line_id > _MAX_UINT64
            ):
                continue
            record_index = _selected_line_record_index(
                selected_line_records,
                line_id,
            )
            if record_index is None:
                continue
            selected_id, count, _coordinate_index, source_line = (
                selected_line_records[record_index]
            )
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
            reannotated_lines.append(
                line.with_source_line(source_line)
            )
            continue
        line_id = line.id
        if (
            type(line_id) is not int
            or line_id < 0
            or line_id > _MAX_UINT64
        ):
            continue
        record_index = _selected_line_record_index(
            selected_line_records,
            line_id,
        )
        if record_index is None:
            continue
        selected_id, count, coordinate_index, _source_line = (
            selected_line_records[record_index]
        )
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
        _line_id, _count, coordinate_index, encoded_source_line = (
            selected_line_records[record_index]
        )
        reannotated_lines.append(
            coordinate_lines[coordinate_index - 1].with_source_line(
                encoded_source_line - 1
                if encoded_source_line > 0
                else None
            )
        )

    return reannotated_lines


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


def refresh_selected_lines_against_source_lines(
    selected_lines: list[LineEntry],
    *,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    lineage: BatchSourceLineage | None = None,
    exact_transforms: tuple[
        BatchSourceExactTransform[BatchSourceSpace, BatchSourceSpace],
        BatchSourceExactTransform[WorktreeSpace, BatchSourceSpace],
    ] | None = None,
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
            return _refresh_selected_line_coordinates(
                selected_lines,
                coordinate_lines,
                selected_line_records,
                transform,
            )
    finally:
        if mapping is not None:
            mapping.close()
