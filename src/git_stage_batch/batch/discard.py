"""Discard batch ownership from target line sequences."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from .merge.baseline_correspondence import (
    build_baseline_correspondence as _build_discard_baseline_correspondence,
)
from .merge.baseline_anchor_matching import (
    acquire_discard_baseline_anchor_pairs as _acquire_discard_baseline_anchor_pairs,
)
from .discard_reversal import (
    reverse_presence_constraints as _reverse_batch_presence_constraints,
)
from .line_matching.line_mapping import LineMapping
from .line_matching.line_range_view import LineRangeView
from .line_matching.match import match_lines
from .line_matching.match_workspace import MatcherWorkspace
from .realization.entries import RealizedEntry as _RealizedEntry
from .realization.entry_storage import (
    RealizedEntries,
    as_realized_entries,
    realized_entry_content_chunks as _realized_entry_content_chunks,
)
from .realization import mapping as _realized_mapping
from ..core.buffer import (
    LineBuffer,
    buffer_has_data,
)
from ..core.line_selection import LineRanges
from ..core.mapped_storage import MappedRecordVector, sort_mapped_records
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MissingAnchorError as _MissingAnchorError,
)
from ..i18n import _, ngettext
from ..core.text_lines import (
    AcquirableLineSequence,
    normalize_line_endings,
    normalize_line_sequence_endings,
)

if TYPE_CHECKING:
    from .ownership.model import BatchOwnership
    from .ownership.absence_claims import AbsenceClaim


def _discard_result_line_ending_from_lines(
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
) -> bytes | None:
    """Choose the line ending style for line sequence discard output."""
    result_line_ending = choose_line_ending(working_lines)
    if result_line_ending is not None:
        return result_line_ending
    if buffer_has_data(baseline_lines):
        return choose_line_ending(baseline_lines)
    return choose_line_ending(source_lines)


def discard_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> LineBuffer:
    """Discard ownership and return a buffer with destination line endings."""
    result_line_ending = _discard_result_line_ending_from_lines(
        working_lines,
        baseline_lines,
        source_lines,
    )
    normalized_source_lines = normalize_line_sequence_endings(source_lines)
    normalized_working_lines = normalize_line_sequence_endings(working_lines)
    normalized_baseline_lines = normalize_line_sequence_endings(baseline_lines)
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _discard_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                normalized_baseline_lines,
            ),
            result_line_ending,
        ),
    )


def _discard_batch_line_chunks(
    source_lines: AcquirableLineSequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: AcquirableLineSequence[bytes],
    baseline_lines: AcquirableLineSequence[bytes],
) -> Iterator[bytes]:
    """Discard ownership from normalized byte-line sequences."""
    with (
        source_lines.acquire_lines() as acquired_source_lines,
        working_lines.acquire_lines() as acquired_working_lines,
        baseline_lines.acquire_lines() as acquired_baseline_lines,
    ):
        yield from _discard_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            acquired_baseline_lines,
        )


def _discard_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: 'BatchOwnership',
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> Iterator[bytes]:
    """Discard ownership from acquired normalized byte-line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    with (
        match_lines(source_lines, working_lines) as source_to_working,
        _acquire_discard_baseline_anchor_pairs(
            source_lines,
            baseline_lines,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=working_lines,
        ) as baseline_anchor_pairs,
    ):
        correspondence = _build_discard_baseline_correspondence(
            baseline_lines,
            source_lines,
            anchor_pairs=baseline_anchor_pairs,
        )

        realized_entries = _build_realized_entries_for_discard(
            source_lines,
            working_lines,
            source_to_working,
        )

    try:
        updated_entries = _reverse_batch_presence_constraints(
            realized_entries,
            presence_line_set,
            correspondence,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        updated_entries = _restore_absence_constraints(
            realized_entries,
            deletion_claims,
        )
        if updated_entries is not realized_entries:
            realized_entries.close()
        realized_entries = updated_entries

        yield from _realized_entry_content_chunks(realized_entries)
    finally:
        realized_entries.close()


def _build_realized_entries_for_discard(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    working_to_source: LineMapping,
) -> RealizedEntries:
    """Build structured entries from working tree with source provenance."""
    result = RealizedEntries()
    _realized_mapping.append_working_range_with_mapping(
        result,
        working_lines,
        working_to_source,
        0,
        len(working_lines),
        LineRanges.empty(),
    )

    return result


def _containing_source_range_index(
    ranges: Sequence[tuple[int, int]],
    source_line: int,
) -> int | None:
    """Return the normalized range containing one source line."""
    lower = 0
    upper = len(ranges)
    while lower < upper:
        middle = (lower + upper) // 2
        if ranges[middle][0] <= source_line:
            lower = middle + 1
        else:
            upper = middle
    range_index = lower - 1
    if range_index < 0 or source_line > ranges[range_index][1]:
        return None
    return range_index


def _normalize_mapped_line_ranges(ranges: MappedRecordVector) -> None:
    """Sort and compact inclusive line ranges in mapped storage."""
    if len(ranges) > 1:
        sort_mapped_records(ranges)
    retained_count = 0
    for source_start, source_end in ranges:
        if retained_count:
            previous_start, previous_end = ranges[retained_count - 1]
            if source_start <= previous_end + 1:
                ranges[retained_count - 1] = (
                    previous_start,
                    max(previous_end, source_end),
                )
                continue
        ranges[retained_count] = (source_start, source_end)
        retained_count += 1
    ranges.truncate(retained_count)


def _build_realized_source_boundary_index(
    workspace: MatcherWorkspace,
    entries: RealizedEntries,
) -> MappedRecordVector:
    """Index every realized source line in storage-backed sorted records."""
    boundaries = workspace.record_vector(len(entries), "QQQ")
    for run in entries.provenance_runs():
        if run.source_start == 0:
            continue
        run_length = run.dest_end - run.dest_start
        for offset in range(run_length):
            boundaries.append((
                run.source_start + offset,
                run.dest_start + offset + 1,
                int(run.is_claimed),
            ))
    sort_mapped_records(boundaries)
    return boundaries


def _first_source_boundary_record(
    boundaries: Sequence[tuple[int, ...]],
    source_line: int,
) -> int:
    """Return the first boundary record for or after one source line."""
    lower = 0
    upper = len(boundaries)
    while lower < upper:
        middle = (lower + upper) // 2
        if boundaries[middle][0] < source_line:
            lower = middle + 1
        else:
            upper = middle
    return lower


def _indexed_boundary_after_source_line(
    boundaries: Sequence[tuple[int, ...]],
    source_line: int | None,
) -> int:
    """Resolve one source boundary with the standard ambiguity semantics."""
    if source_line is None:
        return 0

    first_record = _first_source_boundary_record(boundaries, source_line)
    record_index = first_record
    matching_count = 0
    claimed_count = 0
    matching_boundary = 0
    claimed_boundary = 0
    while (
        record_index < len(boundaries)
        and boundaries[record_index][0] == source_line
    ):
        _record_source_line, boundary, is_claimed = boundaries[record_index]
        matching_count += 1
        matching_boundary = boundary
        if is_claimed:
            claimed_count += 1
            claimed_boundary = boundary
        record_index += 1

    if matching_count == 0:
        raise _MissingAnchorError(
            _(
                "Cannot locate anchor boundary after source line {line}: "
                "anchor not present in realized content"
            ).format(line=source_line)
        )
    if matching_count == 1:
        return matching_boundary
    if claimed_count == 1:
        return claimed_boundary
    if claimed_count == 0:
        raise _AmbiguousAnchorError(
            ngettext(
                "Anchor ambiguity: source line {line} appears {count} time "
                "in realized content but is not claimed",
                "Anchor ambiguity: source line {line} appears {count} times "
                "in realized content but none are claimed",
                matching_count,
            ).format(line=source_line, count=matching_count)
        )
    raise _AmbiguousAnchorError(
        ngettext(
            "Anchor ambiguity: source line {line} claimed {count} time",
            "Anchor ambiguity: source line {line} claimed {count} times",
            claimed_count,
        ).format(line=source_line, count=claimed_count)
    )


def _line_sequence_present_at_boundary(
    lines: Sequence[bytes],
    boundary: int,
    sequence: Sequence[bytes],
) -> bool:
    """Return whether normalized bytes are present at one indexed boundary."""
    if boundary < 0 or boundary + len(sequence) > len(lines):
        return False
    return all(
        normalize_line_endings(lines[boundary + offset])
        == normalize_line_endings(content)
        for offset, content in enumerate(sequence)
    )


def _append_realized_range_from_buffer(
    destination: RealizedEntries,
    source: RealizedEntries,
    source_lines: Sequence[bytes],
    start: int,
    stop: int,
) -> None:
    """Copy one realized range without rescanning editor piece runs."""
    for run in source.provenance_runs(start, stop):
        destination.append_line_range_from(
            source_lines,
            run.dest_start,
            run.dest_end,
            source_line_start=run.source_line_at(run.dest_start),
            target_line_start=run.target_line_at(run.dest_start),
            is_claimed=run.is_claimed,
        )


def _restore_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: list['AbsenceClaim'],
    replacement_restore_records: Sequence[tuple[int, ...]] | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
) -> RealizedEntries:
    """Restore absence constraints at anchored source boundaries."""
    result = as_realized_entries(entries)
    if not deletion_claims:
        return result

    def restored_content_lines(claim_index: int) -> Sequence[bytes]:
        claim = deletion_claims[claim_index]
        if (
            replacement_restore_records is not None
            and replacement_restore_records[claim_index][0] == 3
        ):
            if trusted_target_lines is None:
                raise ValueError(
                    "trusted replacement restoration omitted target lines"
                )
            return LineRangeView(
                trusted_target_lines,
                replacement_restore_records[claim_index][1],
                replacement_restore_records[claim_index][2],
            )
        return normalize_line_sequence_endings(claim.content_lines)

    try:
        indexed_result_lines = LineBuffer.from_line_chunks(
            result.content_chunks()
        )
    except BaseException:
        if result is not entries:
            result.close()
        raise
    indexed_result_is_retained = False
    try:
        with MatcherWorkspace() as workspace:
            source_boundaries = _build_realized_source_boundary_index(
                workspace,
                result,
            )
            insertions = workspace.record_vector(len(deletion_claims), "QQ")
            previous_anchor: int | None | object = object()
            anchor_offset = 0
            for claim_index, claim in enumerate(deletion_claims):
                if (
                    replacement_restore_records is not None
                    and replacement_restore_records[claim_index][0] == 1
                ):
                    continue
                content_lines = restored_content_lines(claim_index)
                try:
                    boundary = _indexed_boundary_after_source_line(
                        source_boundaries,
                        claim.anchor_line,
                    )
                except _MissingAnchorError:
                    continue
                except _AmbiguousAnchorError:
                    raise

                # Split children can share the source line before their old
                # content. Existing children advance within the target;
                # missing children stay queued there in recorded order.
                if claim.anchor_line != previous_anchor:
                    previous_anchor = claim.anchor_line
                    anchor_offset = 0
                boundary += anchor_offset
                if _line_sequence_present_at_boundary(
                    indexed_result_lines,
                    boundary,
                    content_lines,
                ):
                    anchor_offset += len(content_lines)
                    continue
                insertions.append((boundary, claim_index))

            if not insertions:
                return result

            sort_mapped_records(insertions)
            restored_claim_lines = LineBuffer.from_line_chunks(
                line
                for _boundary, claim_index in insertions
                for line in restored_content_lines(claim_index)
            )
            restored_claim_lines_are_retained = False
            try:
                restored = RealizedEntries()
                try:
                    restored.retain_line_buffer(indexed_result_lines)
                    indexed_result_is_retained = True
                    restored.retain_line_buffer(restored_claim_lines)
                    restored_claim_lines_are_retained = True
                    copy_start = 0
                    restored_claim_offset = 0
                    for boundary, claim_index in insertions:
                        if copy_start < boundary:
                            _append_realized_range_from_buffer(
                                restored,
                                result,
                                indexed_result_lines,
                                copy_start,
                                boundary,
                            )
                            copy_start = boundary
                        claim_line_count = len(
                            restored_content_lines(claim_index)
                        )
                        restored_claim_end = (
                            restored_claim_offset + claim_line_count
                        )
                        restored.append_line_range_from(
                            restored_claim_lines,
                            restored_claim_offset,
                            restored_claim_end,
                            source_line_start=None,
                            is_claimed=False,
                        )
                        restored_claim_offset = restored_claim_end
                    if copy_start < len(result):
                        _append_realized_range_from_buffer(
                            restored,
                            result,
                            indexed_result_lines,
                            copy_start,
                            len(result),
                        )
                    if result is not entries:
                        result.close()
                    return restored
                except BaseException:
                    restored.close()
                    raise
            finally:
                if not restored_claim_lines_are_retained:
                    restored_claim_lines.close()
    except BaseException:
        if result is not entries:
            result.close()
        raise
    finally:
        if not indexed_result_is_retained:
            indexed_result_lines.close()
