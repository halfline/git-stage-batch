"""Discard batch ownership from target line sequences."""

from __future__ import annotations

from collections.abc import Container, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, cast

from .merge.baseline_correspondence import (
    build_baseline_correspondence as _build_discard_baseline_correspondence,
)
from .merge.baseline_anchor_matching import (
    acquire_discard_baseline_anchor_pairs as _acquire_discard_baseline_anchor_pairs,
)
from .merge.baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
    replacement_source_range_capacity as _replacement_source_range_capacity,
)
from .ownership.replacement_units import replacement_counts_cover_origin
from .discard_reversal import (
    reverse_presence_constraints as _reverse_batch_presence_constraints,
)
from .line_matching.line_mapping import LineMapping
from .line_matching.line_range_view import LineRangeView
from .line_matching.match import match_lines
from .line_matching.match_workspace import MatcherWorkspace
from .line_matching.occurrence_index import LinePayloadOccurrenceIndex
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
from ..core.coordinates import (
    FileSnapshot,
    WorktreeSpace,
    content_snapshot,
    require_same_snapshot,
)
from ..core.line_selection import LineRanges
from ..core.mapped_storage import MappedRecordVector, sort_mapped_records
from ..core.resource_cleanup import close_resources_preserving_first
from ..core.text_lines import (
    AcquirableLineSequence,
    as_acquirable_line_sequence,
    normalize_line_endings,
    normalize_line_sequence_endings,
)
from ..editor.line_endings import (
    choose_line_ending,
    restore_line_endings_in_chunks,
)
from ..exceptions import (
    AmbiguousAnchorError as _AmbiguousAnchorError,
    MissingAnchorError as _MissingAnchorError,
)
from ..i18n import _, ngettext
from .file_state import BatchFileState

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


def discard_batch_file_state_as_buffer(
    batch_file: BatchFileState,
    target_snapshot: FileSnapshot[WorktreeSpace],
    target_lines: Sequence[bytes],
    *,
    trusted_presence_lines: LineRanges | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    applied_presence_lines: LineRanges | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
) -> LineBuffer:
    """Discard a source-bound batch state from one exact target snapshot."""
    if batch_file.path != target_snapshot.path:
        raise ValueError("discard target path does not match batch file")
    batch_file.validate()
    target_sequence = as_acquirable_line_sequence(target_lines)
    with target_sequence.acquire_lines() as acquired:
        require_same_snapshot(
            target_snapshot,
            content_snapshot(batch_file.path, acquired, space=WorktreeSpace),
        )
    return discard_batch_from_line_sequences_as_buffer(
        batch_file.source_lines,
        batch_file.ownership,
        target_lines,
        batch_file.baseline_lines,
        trusted_presence_lines=trusted_presence_lines,
        trusted_target_lines=trusted_target_lines,
        applied_presence_lines=applied_presence_lines,
        index_preimage_presence_lines=index_preimage_presence_lines,
    )


def discard_batch_from_line_sequences_as_buffer(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    *,
    trusted_presence_lines: LineRanges | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    applied_presence_lines: LineRanges | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
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
    normalized_trusted_target_lines = (
        None
        if trusted_target_lines is None
        else normalize_line_sequence_endings(trusted_target_lines)
    )
    return LineBuffer.from_chunks(
        restore_line_endings_in_chunks(
            _discard_batch_line_chunks(
                normalized_source_lines,
                ownership,
                normalized_working_lines,
                normalized_baseline_lines,
                trusted_presence_lines=trusted_presence_lines,
                trusted_target_lines=normalized_trusted_target_lines,
                applied_presence_lines=applied_presence_lines,
                index_preimage_presence_lines=(index_preimage_presence_lines),
            ),
            result_line_ending,
        ),
    )


def _discard_batch_line_chunks(
    source_lines: AcquirableLineSequence[bytes],
    ownership: "BatchOwnership",
    working_lines: AcquirableLineSequence[bytes],
    baseline_lines: AcquirableLineSequence[bytes],
    *,
    trusted_presence_lines: LineRanges | None = None,
    trusted_target_lines: AcquirableLineSequence[bytes] | None = None,
    applied_presence_lines: LineRanges | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
) -> Iterator[bytes]:
    """Discard ownership from normalized byte-line sequences."""
    with ExitStack() as stack:
        acquired_source_lines = stack.enter_context(source_lines.acquire_lines())
        acquired_working_lines = stack.enter_context(working_lines.acquire_lines())
        acquired_baseline_lines = stack.enter_context(baseline_lines.acquire_lines())
        acquired_trusted_target_lines = (
            None
            if trusted_target_lines is None
            else stack.enter_context(trusted_target_lines.acquire_lines())
        )
        yield from _discard_batch_acquired_line_chunks(
            acquired_source_lines,
            ownership,
            acquired_working_lines,
            acquired_baseline_lines,
            trusted_presence_lines=trusted_presence_lines,
            trusted_target_lines=acquired_trusted_target_lines,
            applied_presence_lines=applied_presence_lines,
            index_preimage_presence_lines=(index_preimage_presence_lines),
        )


def _discard_batch_acquired_line_chunks(
    source_lines: Sequence[bytes],
    ownership: "BatchOwnership",
    working_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    *,
    trusted_presence_lines: LineRanges | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    applied_presence_lines: LineRanges | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
) -> Iterator[bytes]:
    """Discard ownership from acquired normalized byte-line sequences."""
    resolved = ownership.resolve()
    presence_line_set = resolved.presence_line_set
    deletion_claims = resolved.deletion_claims

    with ExitStack() as stack:
        discard_workspace = stack.enter_context(MatcherWorkspace())
        source_to_trusted_target = (
            None
            if trusted_target_lines is None
            else stack.enter_context(match_lines(source_lines, trusted_target_lines))
        )
        trusted_target_to_working = (
            None
            if trusted_target_lines is None
            else stack.enter_context(match_lines(trusted_target_lines, working_lines))
        )
        trusted_anchor_result = stack.enter_context(
            _acquire_trusted_discard_presence_anchors(
                source_lines,
                working_lines,
                presence_line_set,
                trusted_presence_lines,
                trusted_target_to_working=trusted_target_to_working,
                index_preimage_presence_lines=(index_preimage_presence_lines),
            )
        )
        source_to_working = stack.enter_context(
            match_lines(
                source_lines,
                working_lines,
                anchor_pairs=trusted_anchor_result[0],
            )
        )
        preexisting_applied_presence = _trusted_preexisting_applied_presence(
            source_lines,
            working_lines,
            trusted_target_lines,
            presence_line_set,
            applied_presence_lines,
            source_to_working,
            source_to_trusted_target,
            trusted_target_to_working,
        )
        with _acquire_discard_baseline_anchor_pairs(
            source_lines,
            baseline_lines,
            ownership,
            source_to_working_mapping=source_to_working,
            working_lines=working_lines,
        ) as baseline_anchor_pairs:
            correspondence = _build_discard_baseline_correspondence(
                baseline_lines,
                source_lines,
                anchor_pairs=baseline_anchor_pairs,
            )

        (
            replacement_restore_records,
            separately_restored_presence_ranges,
        ) = _replacement_deletion_restore_records(
            discard_workspace,
            ownership,
            len(deletion_claims),
            source_lines,
            working_lines,
            source_to_working,
            trusted_target_lines,
            source_to_trusted_target,
            trusted_target_to_working,
            trusted_anchor_result[1],
            index_preimage_presence_lines,
            preexisting_applied_presence,
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
                indexed_content_lines=working_lines,
                trusted_insertion_lines=trusted_anchor_result[1],
                preserved_presence_lines=(preexisting_applied_presence),
                separately_restored_ranges=(separately_restored_presence_ranges),
            )
            if updated_entries is not realized_entries:
                try:
                    realized_entries.close()
                except BaseException:
                    close_resources_preserving_first(
                        (updated_entries,),
                        suppress_errors=True,
                    )
                    raise
            realized_entries = updated_entries

            updated_entries = _restore_absence_constraints(
                realized_entries,
                deletion_claims,
                replacement_restore_records,
                trusted_target_lines,
            )
            if updated_entries is not realized_entries:
                try:
                    realized_entries.close()
                except BaseException:
                    close_resources_preserving_first(
                        (updated_entries,),
                        suppress_errors=True,
                    )
                    raise
            realized_entries = updated_entries

            yield from _realized_entry_content_chunks(realized_entries)
        except BaseException:
            close_resources_preserving_first(
                (realized_entries,),
                suppress_errors=True,
            )
            raise
        else:
            close_resources_preserving_first((realized_entries,))


@contextmanager
def _acquire_trusted_discard_presence_anchors(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_lines: LineRanges,
    trusted_presence_lines: LineRanges | None,
    *,
    trusted_target_to_working: LineMapping | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
) -> Iterator[tuple[Sequence[tuple[int, int]], LineRanges]]:
    """Return exact equal-line anchors authorized by fresh apply provenance."""
    workspace = MatcherWorkspace()
    scope_completed = False
    try:
        anchors = workspace.record_vector(presence_lines.count(), "QQ")
        if not trusted_presence_lines:
            yield cast(Sequence[tuple[int, int]], anchors), LineRanges.empty()
            scope_completed = True
            return

        trusted = presence_lines.intersection(trusted_presence_lines)
        occurrence_index: LinePayloadOccurrenceIndex | None = None
        introduced_occurrence_index: LinePayloadOccurrenceIndex | None = None
        for start, end in trusted.ranges():
            for source_line in range(start, end + 1):
                if source_line > len(source_lines):
                    continue
                content = source_lines[source_line - 1]
                effective_occurrence_index = occurrence_index
                if (
                    index_preimage_presence_lines is not None
                    and source_line in index_preimage_presence_lines
                    and trusted_target_to_working is not None
                ):
                    if introduced_occurrence_index is None:
                        introduced_occurrence_index = LinePayloadOccurrenceIndex(
                            workspace,
                            working_lines,
                            normalize_payloads=False,
                            target_indexes=(
                                target_index
                                for target_index in range(len(working_lines))
                                if trusted_target_to_working.get_source_line_from_target_line(
                                    target_index + 1
                                )
                                is None
                            ),
                        )
                    effective_occurrence_index = introduced_occurrence_index
                elif effective_occurrence_index is None:
                    occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        working_lines,
                        normalize_payloads=False,
                    )
                    effective_occurrence_index = occurrence_index
                assert effective_occurrence_index is not None
                if effective_occurrence_index.occurrence_count(content) != 1:
                    continue
                target_index = next(
                    effective_occurrence_index.matching_line_indexes(content)
                )
                anchors.append((source_line, target_index + 1))
        sort_mapped_records(anchors)
        if not _strictly_increasing_anchor_pairs(anchors):
            anchors.truncate(0)
        anchored_lines = LineRanges.from_ranges(
            _source_ranges_from_sorted_anchors(anchors)
        )
        yield cast(Sequence[tuple[int, int]], anchors), anchored_lines
        scope_completed = True
    finally:
        close_resources_preserving_first(
            (workspace,),
            suppress_errors=not scope_completed,
        )


def _strictly_increasing_anchor_pairs(
    anchors: Sequence[tuple[int, ...]],
) -> bool:
    previous_source = 0
    previous_target = 0
    for source_line, target_line in anchors:
        if source_line <= previous_source or target_line <= previous_target:
            return False
        previous_source = source_line
        previous_target = target_line
    return True


def _source_ranges_from_sorted_anchors(
    anchors: Sequence[tuple[int, ...]],
) -> Iterator[tuple[int, int]]:
    """Yield compact source ranges from source-ordered anchor pairs."""
    pending_start: int | None = None
    pending_end: int | None = None
    for source_line, _target_line in anchors:
        if pending_start is None or pending_end is None:
            pending_start = pending_end = source_line
            continue
        if source_line == pending_end + 1:
            pending_end = source_line
            continue
        yield pending_start, pending_end
        pending_start = pending_end = source_line
    if pending_start is not None and pending_end is not None:
        yield pending_start, pending_end


class _TrustedPreexistingAppliedPresence(Container[int]):
    """Membership proof for applied source lines inherited from the index."""

    def __init__(
        self,
        source_lines: Sequence[bytes],
        working_lines: Sequence[bytes],
        trusted_target_lines: Sequence[bytes],
        owned_presence_lines: LineRanges,
        applied_presence_lines: LineRanges,
        source_to_working: LineMapping,
        source_to_trusted_target: LineMapping,
        trusted_target_to_working: LineMapping,
    ) -> None:
        self._source_lines = source_lines
        self._working_lines = working_lines
        self._trusted_target_lines = trusted_target_lines
        self._owned_presence_lines = owned_presence_lines
        self._applied_presence_lines = applied_presence_lines
        self._source_to_working = source_to_working
        self._source_to_trusted_target = source_to_trusted_target
        self._trusted_target_to_working = trusted_target_to_working

    def __contains__(self, source_line: object) -> bool:
        if (
            type(source_line) is not int
            or source_line not in self._owned_presence_lines
            or source_line not in self._applied_presence_lines
        ):
            return False
        trusted_line = self._source_to_trusted_target.get_target_line_from_source_line(
            source_line
        )
        working_line = self._source_to_working.get_target_line_from_source_line(
            source_line
        )
        return (
            trusted_line is not None
            and working_line is not None
            and self._trusted_target_to_working.get_target_line_from_source_line(
                trusted_line
            )
            == working_line
            and self._source_lines[source_line - 1]
            == self._trusted_target_lines[trusted_line - 1]
            == self._working_lines[working_line - 1]
        )


def _trusted_preexisting_applied_presence(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    presence_lines: LineRanges,
    applied_presence_lines: LineRanges | None,
    source_to_working: LineMapping,
    source_to_trusted_target: LineMapping | None,
    trusted_target_to_working: LineMapping | None,
) -> Container[int] | None:
    """Return a storage-bounded proof for presence inherited from the index."""
    if (
        trusted_target_lines is None
        or not applied_presence_lines
        or source_to_trusted_target is None
        or trusted_target_to_working is None
    ):
        return None
    return _TrustedPreexistingAppliedPresence(
        source_lines,
        working_lines,
        trusted_target_lines,
        presence_lines,
        applied_presence_lines,
        source_to_working,
        source_to_trusted_target,
        trusted_target_to_working,
    )


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


def _trusted_index_replacement_restore_bounds(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    unit_index: int,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    source_to_working: LineMapping,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target: LineMapping | None,
    trusted_target_to_working: LineMapping | None,
    trusted_anchored_lines: LineRanges,
    index_preimage_presence_lines: LineRanges | None,
) -> tuple[int, int] | None:
    """Return the exact index preimage for one freshly applied replacement."""
    if (
        trusted_target_lines is None
        or source_to_trusted_target is None
        or trusted_target_to_working is None
        or not index_preimage_presence_lines
    ):
        return None
    unit = ownership.replacement_units[unit_index]
    origin = unit.origin
    if origin is None or len(unit.deletion_indices) != 1:
        return None
    deletion_index = unit.deletion_indices[0]
    if (
        type(deletion_index) is not int
        or deletion_index < 0
        or deletion_index >= len(ownership.deletions)
    ):
        return None
    claim = ownership.deletions[deletion_index]
    if claim.baseline_reference != origin.baseline_reference:
        return None

    claimed_ranges = _collect_replacement_source_ranges(
        workspace,
        unit.presence_lines,
    )
    if claimed_ranges is None:
        return None
    try:
        if len(claimed_ranges) != 1:
            return None
        source_start, source_end = claimed_ranges[0]
        selected_count = source_end - source_start + 1
        if (
            source_start <= 1
            or source_end >= len(source_lines)
            or not replacement_counts_cover_origin(
                origin,
                selected_count,
                len(claim.content_lines),
            )
            or any(
                source_line not in trusted_anchored_lines
                or source_line not in index_preimage_presence_lines
                for source_line in range(source_start, source_end + 1)
            )
        ):
            return None

        before_source_line = source_start - 1
        after_source_line = source_end + 1
        working_before = source_to_working.get_target_line_from_source_line(
            before_source_line
        )
        working_after = source_to_working.get_target_line_from_source_line(
            after_source_line
        )
        trusted_before = source_to_trusted_target.get_target_line_from_source_line(
            before_source_line
        )
        trusted_after = source_to_trusted_target.get_target_line_from_source_line(
            after_source_line
        )
        if (
            working_before is None
            or working_after is None
            or trusted_before is None
            or trusted_after is None
            or working_after - working_before - 1 != selected_count
            or trusted_after <= trusted_before
            or trusted_target_to_working.get_target_line_from_source_line(
                trusted_before
            )
            != working_before
            or trusted_target_to_working.get_target_line_from_source_line(trusted_after)
            != working_after
        ):
            return None
        working_start = working_before
        for offset, source_line in enumerate(range(source_start, source_end + 1)):
            working_line = working_start + offset + 1
            if (
                source_to_working.get_target_line_from_source_line(source_line)
                != working_line
                or source_lines[source_line - 1] != working_lines[working_line - 1]
            ):
                return None
        return trusted_before, trusted_after - 1
    finally:
        workspace.close_resource(claimed_ranges)


def _trusted_index_replacement_group_restore_bounds(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    group_start: int,
    group_end: int,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    source_to_working: LineMapping,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target: LineMapping | None,
    trusted_target_to_working: LineMapping | None,
    index_preimage_presence_lines: LineRanges | None,
) -> tuple[bool, tuple[int, int] | None]:
    """Verify and locate one collectively authorized index preimage group."""
    if (
        trusted_target_lines is None
        or source_to_trusted_target is None
        or trusted_target_to_working is None
        or not index_preimage_presence_lines
    ):
        return False, None

    first_source_line: int | None = None
    next_source_line: int | None = None
    selected_line_count = 0
    deletion_line_count = 0
    next_baseline_after_line: int | None = None
    for unit_index in range(group_start, group_end):
        unit = ownership.replacement_units[unit_index]
        if len(unit.deletion_indices) != 1:
            return False, None
        deletion_index = unit.deletion_indices[0]
        if (
            type(deletion_index) is not int
            or deletion_index < 0
            or deletion_index >= len(ownership.deletions)
        ):
            return False, None
        claim = ownership.deletions[deletion_index]
        claim_reference = claim.baseline_reference
        if (
            not claim.content_lines
            or claim_reference is None
            or not claim_reference.has_after_line
            or claim_reference.after_line is None
            or (
                next_baseline_after_line is not None
                and claim_reference.after_line != next_baseline_after_line
            )
        ):
            return False, None

        claimed_ranges = _collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        if claimed_ranges is None:
            return False, None
        try:
            if len(claimed_ranges) != 1:
                return False, None
            source_start, source_end = claimed_ranges[0]
            if (
                source_end > len(source_lines)
                or (next_source_line is not None and source_start != next_source_line)
                or any(
                    source_line not in index_preimage_presence_lines
                    for source_line in range(source_start, source_end + 1)
                )
            ):
                return False, None
            if first_source_line is None:
                first_source_line = source_start
            next_source_line = source_end + 1
            selected_line_count += source_end - source_start + 1
        finally:
            workspace.close_resource(claimed_ranges)

        deletion_line_count += len(claim.content_lines)
        next_baseline_after_line = claim_reference.after_line + len(claim.content_lines)

    if (
        first_source_line is None
        or next_source_line is None
        or first_source_line <= 1
        or next_source_line > len(source_lines)
    ):
        return False, None

    before_source_line = first_source_line - 1
    after_source_line = next_source_line
    working_before = source_to_working.get_target_line_from_source_line(
        before_source_line
    )
    working_after = source_to_working.get_target_line_from_source_line(
        after_source_line
    )
    trusted_before = source_to_trusted_target.get_target_line_from_source_line(
        before_source_line
    )
    trusted_after = source_to_trusted_target.get_target_line_from_source_line(
        after_source_line
    )
    if (
        working_before is None
        or working_after is None
        or trusted_before is None
        or trusted_after is None
        or working_after - working_before - 1 != selected_line_count
        or trusted_after - trusted_before - 1 != deletion_line_count
        or trusted_target_to_working.get_target_line_from_source_line(trusted_before)
        != working_before
        or trusted_target_to_working.get_target_line_from_source_line(trusted_after)
        != working_after
    ):
        return True, None

    working_start = working_before
    source_line = first_source_line
    for offset in range(selected_line_count):
        working_line = working_start + offset + 1
        if (
            source_to_working.get_target_line_from_source_line(source_line)
            != working_line
            or source_to_trusted_target.get_target_line_from_source_line(source_line)
            is not None
            or source_lines[source_line - 1] != working_lines[working_line - 1]
        ):
            return True, None
        source_line += 1
    return True, (trusted_before, trusted_after - 1)


def _replacement_deletion_restore_records(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    deletion_count: int,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    source_to_working: LineMapping,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target: LineMapping | None,
    trusted_target_to_working: LineMapping | None,
    trusted_anchored_lines: LineRanges,
    index_preimage_presence_lines: LineRanges | None,
    preserved_presence_lines: Container[int] | None,
) -> tuple[Sequence[tuple[int, ...]], Sequence[tuple[int, ...]]]:
    """Mark replacement old sides whose selected new side is realized.

    Zero denotes a non-replacement claim, one a replacement that is not live,
    two a replacement whose historical old side should be restored, and three
    a freshly applied replacement whose exact index preimage should be restored.
    """
    records = workspace.record_vector(
        deletion_count,
        "BQQ",
        length=deletion_count,
    )
    separately_restored_ranges = workspace.record_vector(
        deletion_count
        + sum(
            _replacement_source_range_capacity(unit.presence_lines)
            for unit in ownership.replacement_units
        ),
        "QQ",
    )
    handled_units = workspace.record_vector(
        len(ownership.replacement_units),
        "B",
        length=len(ownership.replacement_units),
    )
    unit_index = 0
    while unit_index < len(ownership.replacement_units):
        group_end = unit_index + 1
        origin = ownership.replacement_units[unit_index].origin
        if origin is not None:
            while (
                group_end < len(ownership.replacement_units)
                and ownership.replacement_units[group_end].origin == origin
            ):
                group_end += 1
        if origin is not None and group_end == unit_index + 1:
            unit_index = group_end
            continue

        has_authorization, restore_bounds = (
            _trusted_index_replacement_group_restore_bounds(
                workspace,
                ownership,
                unit_index,
                group_end,
                source_lines,
                working_lines,
                source_to_working,
                trusted_target_lines,
                source_to_trusted_target,
                trusted_target_to_working,
                index_preimage_presence_lines,
            )
        )
        if has_authorization:
            if restore_bounds is None:
                raise _AmbiguousAnchorError(
                    _(
                        "Cannot discard an exact applied replacement "
                        "because its live occurrence is ambiguous"
                    )
                )
            restore_position, restore_end = restore_bounds
            for group_unit_index in range(unit_index, group_end):
                unit = ownership.replacement_units[group_unit_index]
                deletion_index = unit.deletion_indices[0]
                claim_line_count = len(
                    ownership.deletions[deletion_index].content_lines
                )
                next_restore_position = restore_position + claim_line_count
                if next_restore_position > restore_end:
                    raise _AmbiguousAnchorError(
                        _(
                            "Cannot discard an exact applied replacement "
                            "because its live occurrence is ambiguous"
                        )
                    )
                records[deletion_index] = (
                    3,
                    restore_position,
                    next_restore_position,
                )
                handled_units[group_unit_index] = (1,)
                claimed_ranges = _collect_replacement_source_ranges(
                    workspace,
                    unit.presence_lines,
                )
                assert claimed_ranges is not None
                try:
                    for source_start, source_end in claimed_ranges:
                        separately_restored_ranges.append((source_start, source_end))
                finally:
                    workspace.close_resource(claimed_ranges)
                restore_position = next_restore_position
            if restore_position != restore_end:
                raise _AmbiguousAnchorError(
                    _(
                        "Cannot discard an exact applied replacement "
                        "because its live occurrence is ambiguous"
                    )
                )
        unit_index = group_end

    for unit_index, unit in enumerate(ownership.replacement_units):
        if handled_units[unit_index][0]:
            continue
        claimed_ranges = _collect_replacement_source_ranges(
            workspace,
            unit.presence_lines,
        )
        has_mapped_presence = False
        has_exact_preimage_authorization = False
        try:
            if claimed_ranges is not None:
                has_mapped_presence = any(
                    source_to_working.get_target_line_from_source_line(source_line)
                    is not None
                    and (
                        preserved_presence_lines is None
                        or source_line not in preserved_presence_lines
                    )
                    for source_start, source_end in claimed_ranges
                    for source_line in range(source_start, source_end + 1)
                )
                if (
                    index_preimage_presence_lines
                    and len(claimed_ranges) == 1
                    and unit.origin is not None
                    and len(unit.deletion_indices) == 1
                ):
                    deletion_index = unit.deletion_indices[0]
                    source_start, source_end = claimed_ranges[0]
                    has_exact_preimage_authorization = (
                        type(deletion_index) is int
                        and 0 <= deletion_index < len(ownership.deletions)
                        and replacement_counts_cover_origin(
                            unit.origin,
                            source_end - source_start + 1,
                            len(ownership.deletions[deletion_index].content_lines),
                        )
                        and all(
                            source_line in index_preimage_presence_lines
                            for source_line in range(
                                source_start,
                                source_end + 1,
                            )
                        )
                    )
            flag = 2 if has_mapped_presence else 1
            trusted_restore_bounds = (
                None
                if not has_exact_preimage_authorization
                else _trusted_index_replacement_restore_bounds(
                    workspace,
                    ownership,
                    unit_index,
                    source_lines,
                    working_lines,
                    source_to_working,
                    trusted_target_lines,
                    source_to_trusted_target,
                    trusted_target_to_working,
                    trusted_anchored_lines,
                    index_preimage_presence_lines,
                )
            )
            if has_exact_preimage_authorization:
                if trusted_restore_bounds is None:
                    raise _AmbiguousAnchorError(
                        _(
                            "Cannot discard an exact applied replacement "
                            "because its live occurrence is ambiguous"
                        )
                    )
                flag = 3
            restore_start, restore_end = trusted_restore_bounds or (0, 0)
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is int
                    and 0 <= deletion_index < deletion_count
                    and (
                        flag == 3
                        or (
                            records[deletion_index][0] != 3
                            and (flag == 2 or records[deletion_index][0] == 0)
                        )
                    )
                ):
                    records[deletion_index] = (
                        flag,
                        restore_start,
                        restore_end,
                    )

            has_restored_deletion = any(
                type(deletion_index) is int
                and 0 <= deletion_index < deletion_count
                and records[deletion_index][0] in (2, 3)
                for deletion_index in unit.deletion_indices
            )
            if (
                has_mapped_presence
                and has_restored_deletion
                and claimed_ranges is not None
            ):
                for source_start, source_end in claimed_ranges:
                    separately_restored_ranges.append((source_start, source_end))
        finally:
            if claimed_ranges is not None:
                workspace.close_resource(claimed_ranges)

    _append_live_legacy_replacement_restore_records(
        workspace,
        ownership,
        source_to_working,
        preserved_presence_lines,
        records,
        separately_restored_ranges,
    )
    _normalize_mapped_line_ranges(separately_restored_ranges)
    workspace.close_resource(handled_units)
    return (
        cast(Sequence[tuple[int, ...]], records),
        cast(Sequence[tuple[int, ...]], separately_restored_ranges),
    )


def _append_live_legacy_replacement_restore_records(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    source_to_working: LineMapping,
    preserved_presence_lines: Container[int] | None,
    records: MappedRecordVector,
    separately_restored_ranges: MappedRecordVector,
) -> None:
    """Couple live immediate-source replacements absent from old metadata.

    Legacy batches represented a replacement as a deletion anchored directly
    before a contiguous presence range.  Candidate suffixes within each
    disjoint presence range are scanned together from right to left, so even
    adversarial overlapping legacy claims inspect each source line at most
    once per range.
    """
    presence_ranges = ownership.presence_line_set().ranges()
    if not presence_ranges:
        return

    candidates = workspace.record_vector(len(ownership.deletions), "QQQ")
    try:
        for deletion_index, claim in enumerate(ownership.deletions):
            if records[deletion_index][0] or not claim.content_lines:
                continue
            anchor_line = claim.anchor_line
            if anchor_line is None:
                replacement_start = 1
            elif type(anchor_line) is int and anchor_line >= 0:
                replacement_start = anchor_line + 1
            else:
                continue

            range_index = _containing_source_range_index(
                presence_ranges,
                replacement_start,
            )
            if range_index is None:
                continue
            range_start, range_end = presence_ranges[range_index]
            if not range_start <= replacement_start <= range_end:
                continue
            candidates.append(
                (
                    range_index,
                    replacement_start,
                    deletion_index,
                )
            )

        if not candidates:
            return
        sort_mapped_records(candidates)

        candidate_index = len(candidates) - 1
        while candidate_index >= 0:
            range_index = candidates[candidate_index][0]
            _range_start, range_end = presence_ranges[range_index]
            next_source_line = range_end
            suffix_is_live = False
            while (
                candidate_index >= 0 and candidates[candidate_index][0] == range_index
            ):
                _, replacement_start, deletion_index = candidates[candidate_index]
                while next_source_line >= replacement_start:
                    if source_to_working.get_target_line_from_source_line(
                        next_source_line
                    ) is not None and (
                        preserved_presence_lines is None
                        or next_source_line not in preserved_presence_lines
                    ):
                        suffix_is_live = True
                    next_source_line -= 1
                records[deletion_index] = (
                    2 if suffix_is_live else 1,
                    0,
                    0,
                )
                if suffix_is_live:
                    separately_restored_ranges.append(
                        (
                            replacement_start,
                            range_end,
                        )
                    )
                candidate_index -= 1
    finally:
        workspace.close_resource(candidates)


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
            boundaries.append(
                (
                    run.source_start + offset,
                    run.dest_start + offset + 1,
                    int(run.is_claimed),
                )
            )
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
    while record_index < len(boundaries) and boundaries[record_index][0] == source_line:
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


def _restore_absence_constraints(
    entries: Sequence[_RealizedEntry],
    deletion_claims: list["AbsenceClaim"],
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
                raise ValueError("trusted replacement restoration omitted target lines")
            return LineRangeView(
                trusted_target_lines,
                replacement_restore_records[claim_index][1],
                replacement_restore_records[claim_index][2],
            )
        return normalize_line_sequence_endings(claim.content_lines)

    indexed_result_lines: LineBuffer | None = None
    restored_claim_lines: LineBuffer | None = None
    restored: RealizedEntries | None = None
    workspace: MatcherWorkspace | None = None
    try:
        indexed_result_lines = LineBuffer.from_line_chunks(result.content_chunks())
        workspace = MatcherWorkspace()
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
            workspace.close()
            workspace = None
            indexed_result_lines.close()
            indexed_result_lines = None
            return result

        sort_mapped_records(insertions)
        restored_claim_lines = LineBuffer.from_line_chunks(
            line
            for _boundary, claim_index in insertions
            for line in restored_content_lines(claim_index)
        )
        restored = RealizedEntries()
        indexed_content_lines = indexed_result_lines
        restored_content_buffer = restored_claim_lines
        restored.retain_line_buffer(indexed_content_lines)
        indexed_result_lines = None
        restored.retain_line_buffer(restored_content_buffer)
        restored_claim_lines = None
        copy_start = 0
        restored_claim_offset = 0
        for boundary, claim_index in insertions:
            if copy_start < boundary:
                restored.copy_provenance_slice_from(
                    result,
                    indexed_content_lines,
                    copy_start,
                    boundary,
                )
                copy_start = boundary
            claim_line_count = len(restored_content_lines(claim_index))
            restored_claim_end = restored_claim_offset + claim_line_count
            restored.append_line_range_from(
                restored_content_buffer,
                restored_claim_offset,
                restored_claim_end,
                source_line_start=None,
                is_claimed=False,
            )
            restored_claim_offset = restored_claim_end
        if copy_start < len(result):
            restored.copy_provenance_slice_from(
                result,
                indexed_content_lines,
                copy_start,
                len(result),
            )
        if result is not entries:
            result.close()
        workspace.close()
        workspace = None
        return restored
    except BaseException:
        close_resources_preserving_first(
            (
                restored,
                restored_claim_lines,
                indexed_result_lines,
                workspace,
                result if result is not entries else None,
            ),
            suppress_errors=True,
        )
        raise
