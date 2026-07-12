"""Presence-constraint reversal for batch discard."""

from __future__ import annotations

from collections.abc import Sequence

from .merge.baseline_correspondence import (
    BaselineCorrespondence as _BaselineCorrespondence,
    RegionKind as _RegionKind,
)
from .realization.entries import RealizedEntry as _RealizedEntry
from .realization.entry_storage import (
    RealizedEntries,
    realized_entry_source_line_at,
)
from ..core.line_selection import LineRanges, LineSelection, coerce_line_ranges
from ..exceptions import MergeError as _MergeError
from ..i18n import _


def _count_lines_in_range(
    line_selection: LineSelection,
    start_line: int,
    end_line: int,
) -> int:
    return coerce_line_ranges(line_selection).count(start_line, end_line)


def reverse_presence_constraints(
    entries: Sequence[_RealizedEntry],
    presence_line_set: LineSelection,
    correspondence: _BaselineCorrespondence,
) -> RealizedEntries:
    """Replace or remove batch-owned claimed lines during discard."""
    result = RealizedEntries()
    processed_replace_regions: set[int] = set()

    def flush_copy(start: int | None, stop: int) -> None:
        if start is not None and start < stop:
            result.copy_slice_from(entries, start, stop)

    def restore_source_line(source_line: int) -> None:
        region = correspondence.get_region_for_source_line(source_line)

        if region is None:
            raise _MergeError(
                _(
                    "Cannot discard source line {line}: "
                    "no baseline restoration region found"
                ).format(line=source_line)
            )

        if region.kind in (_RegionKind.EQUAL, _RegionKind.REPLACE_LINE_BY_LINE):
            offset = source_line - region.source_start_line
            if 0 <= offset < len(region.baseline_lines):
                result.append_line_range_from(
                    region.baseline_lines,
                    offset,
                    offset + 1,
                    source_line_start=None,
                    is_claimed=False,
                )
            else:
                raise _MergeError(
                    _(
                        "Source line {line} offset {offset} "
                        "outside region bounds"
                    ).format(line=source_line, offset=offset)
                )

        elif region.kind == _RegionKind.INSERT:
            pass

        elif region.kind == _RegionKind.REPLACE_BY_HUNK:
            if region.region_id not in processed_replace_regions:
                total_lines_in_region = (
                    region.source_end_line - region.source_start_line + 1
                )
                claimed_line_count = _count_lines_in_range(
                    presence_line_set,
                    region.source_start_line,
                    region.source_end_line,
                )

                if claimed_line_count != total_lines_in_region:
                    raise _MergeError(
                        _(
                            "Cannot discard partial ownership of by-hunk "
                            "replace region (source lines {start}-{end}): "
                            "batch owns {owned} of {total} lines"
                        ).format(
                            start=region.source_start_line,
                            end=region.source_end_line,
                            owned=claimed_line_count,
                            total=total_lines_in_region,
                        )
                    )

                result.append_line_range_from(
                    region.baseline_lines,
                    0,
                    len(region.baseline_lines),
                    source_line_start=None,
                    is_claimed=False,
                )
                processed_replace_regions.add(region.region_id)

        else:
            raise _MergeError(
                _("Unknown region kind: {kind}").format(kind=region.kind)
            )

    copy_start: int | None = 0

    if isinstance(entries, RealizedEntries):
        presence_lines = coerce_line_ranges(presence_line_set)
        for run in entries.provenance_runs():
            if run.source_start == 0:
                continue

            run_length = run.dest_end - run.dest_start
            run_source_end = run.source_start + run_length - 1
            selected_lines = presence_lines.intersection(
                LineRanges.from_ranges([(run.source_start, run_source_end)])
            )
            if not selected_lines:
                continue

            for selected_start, selected_end in selected_lines.ranges():
                for source_line in range(selected_start, selected_end + 1):
                    index = run.dest_start + (source_line - run.source_start)
                    flush_copy(copy_start, index)
                    copy_start = None
                    restore_source_line(source_line)
                    copy_start = index + 1

        if copy_start is not None:
            flush_copy(copy_start, len(entries))

        return result

    for index in range(len(entries)):
        source_line = realized_entry_source_line_at(entries, index)
        if source_line is not None and source_line in presence_line_set:
            flush_copy(copy_start, index)
            copy_start = None
            restore_source_line(source_line)
            copy_start = index + 1
        else:
            if copy_start is None:
                copy_start = index

    if copy_start is not None:
        flush_copy(copy_start, len(entries))

    return result
