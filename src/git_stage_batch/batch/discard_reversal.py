"""Presence-constraint reversal for batch discard."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Container, Sequence

from .merge.baseline_correspondence import (
    BaselineCorrespondence as _BaselineCorrespondence,
    RegionKind as _RegionKind,
)
from .realization.entries import RealizedEntry as _RealizedEntry
from .realization.entry_storage import (
    RealizedEntries,
    realized_entry_source_line_at,
)
from ..core.line_selection import (
    LineRanges,
    LineSelection,
    coerce_line_ranges,
    sorted_line_ranges_contain,
)
from ..core.resource_cleanup import close_resources_preserving_first
from ..exceptions import MergeError as _MergeError
from ..i18n import _, ngettext


def _normalized_ranges_overlap(
    ranges: Sequence[tuple[int, int]],
    start_line: int,
    end_line: int,
) -> bool:
    """Return whether normalized inclusive ranges overlap one interval."""
    range_index = bisect_left(ranges, (end_line + 1,)) - 1
    return range_index >= 0 and ranges[range_index][1] >= start_line


def reverse_presence_constraints(
    entries: Sequence[_RealizedEntry],
    presence_line_set: LineSelection,
    correspondence: _BaselineCorrespondence,
    *,
    indexed_content_lines: Sequence[bytes] | None = None,
    trusted_insertion_lines: LineSelection | None = None,
    preserved_presence_lines: Container[int] | None = None,
    separately_restored_ranges: Sequence[tuple[int, ...]] = (),
) -> RealizedEntries:
    """Replace or remove batch-owned claimed lines during discard."""
    result = RealizedEntries()
    processed_replace_regions: set[int] = set()
    trusted_lines = (
        LineRanges.empty()
        if trusted_insertion_lines is None
        else coerce_line_ranges(trusted_insertion_lines)
    )
    trusted_ranges = trusted_lines.ranges()

    def flush_copy(start: int | None, stop: int) -> None:
        if start is not None and start < stop:
            if (
                isinstance(entries, RealizedEntries)
                and indexed_content_lines is not None
            ):
                result.copy_provenance_slice_from(
                    entries,
                    indexed_content_lines,
                    start,
                    stop,
                )
            else:
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
            if sorted_line_ranges_contain(
                separately_restored_ranges,
                source_line,
            ):
                return
            if _normalized_ranges_overlap(
                trusted_ranges,
                region.source_start_line,
                region.source_end_line,
            ):
                # A fresh applied-output anchor makes whole-hunk restoration
                # unsafe: it could restore historical text for neighboring
                # transformed replacements.  Every realized owned line in
                # this hunk must instead have a coupled deletion restoration.
                raise _MergeError(
                    _(
                        "Cannot discard a baseline replacement hunk that "
                        "mixes applied lines with ownership lacking an "
                        "independent old side (source lines {start}-{end})"
                    ).format(
                        start=region.source_start_line,
                        end=region.source_end_line,
                    )
                )
            if region.region_id not in processed_replace_regions:
                total_lines_in_region = (
                    region.source_end_line - region.source_start_line + 1
                )
                claimed_line_count = presence_lines.count(
                    region.source_start_line,
                    region.source_end_line,
                )

                if claimed_line_count != total_lines_in_region:
                    raise _MergeError(
                        ngettext(
                            "Cannot discard partial ownership of by-hunk "
                            "replace region (source lines {start}-{end}): "
                            "batch owns {owned} of {total} line",
                            "Cannot discard partial ownership of by-hunk "
                            "replace region (source lines {start}-{end}): "
                            "batch owns {owned} of {total} lines",
                            total_lines_in_region,
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

    try:
        copy_start: int | None = 0

        presence_lines = coerce_line_ranges(presence_line_set)
        if isinstance(entries, RealizedEntries):
            for run in entries.provenance_runs():
                if run.source_start == 0:
                    continue

                run_length = run.dest_end - run.dest_start
                run_source_end = run.source_start + run_length - 1
                selected_lines = presence_lines.intersection(
                    LineRanges.from_ranges((
                        (run.source_start, run_source_end),
                    ))
                )
                if not selected_lines:
                    continue

                for selected_start, selected_end in selected_lines.ranges():
                    for source_line in range(
                        selected_start,
                        selected_end + 1,
                    ):
                        if (
                            preserved_presence_lines is not None
                            and source_line in preserved_presence_lines
                        ):
                            continue
                        index = (
                            run.dest_start
                            + source_line
                            - run.source_start
                        )
                        flush_copy(copy_start, index)
                        copy_start = None
                        restore_source_line(source_line)
                        copy_start = index + 1

            if copy_start is not None:
                flush_copy(copy_start, len(entries))

            return result

        for index in range(len(entries)):
            entry_source_line = realized_entry_source_line_at(entries, index)
            if (
                entry_source_line is not None
                and entry_source_line in presence_lines
                and (
                    preserved_presence_lines is None
                    or entry_source_line not in preserved_presence_lines
                )
            ):
                flush_copy(copy_start, index)
                copy_start = None
                restore_source_line(entry_source_line)
                copy_start = index + 1
            elif copy_start is None:
                copy_start = index

        if copy_start is not None:
            flush_copy(copy_start, len(entries))

        return result
    except BaseException:
        close_resources_preserving_first(
            (result,),
            suppress_errors=True,
        )
        raise
