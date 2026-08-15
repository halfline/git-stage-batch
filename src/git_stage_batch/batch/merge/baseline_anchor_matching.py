"""Validate baseline-coordinate anchors against merge targets."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from ...core.text_lines import normalize_line_sequence_endings
from ...exceptions import MergeError as _MergeError
from ...i18n import _
from .baseline_reference_positions import (
    baseline_reference_absence_position as _find_baseline_absence_position,
    baseline_reference_insertion_position as _find_baseline_insertion_position,
)
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
)
from .presence_reference_index import EffectivePresenceReferenceIndex
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload as _reference_line_payload,
)
from ..line_matching.sequence_equality import (
    line_slice_equals as _line_slice_matches,
)
from ..ownership.replacement_units import (
    replacement_counts_cover_origin as _replacement_counts_cover_origin,
)
from ..ownership.references import BaselineReference

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.replacement_units import ReplacementUnitOrigin


BaselineRemovalEdit = tuple[int, int]
_BOUNDARY_IDENTITY_CANDIDATE_LIMIT = 16


def _sort_and_validate_anchor_pairs(
    anchor_pairs: MappedRecordVector,
) -> bool:
    """Compact duplicate anchors and report collective coherence."""
    sort_mapped_records(anchor_pairs)
    previous_pair: tuple[int, int] | None = None
    previous_source = 0
    previous_target = 0
    anchor_count = 0
    for pair_record in anchor_pairs:
        source_line, target_line = pair_record
        pair = (source_line, target_line)
        if pair == previous_pair:
            continue
        if source_line <= previous_source or target_line <= previous_target:
            anchor_pairs.truncate(0)
            return False
        anchor_pairs[anchor_count] = pair
        anchor_count += 1
        previous_pair = pair
        previous_source = source_line
        previous_target = target_line

    anchor_pairs.truncate(anchor_count)
    return True


def _line_range_is_contained(
    ranges: Sequence[tuple[int, int]],
    range_starts: Sequence[int],
    start: int,
    end: int,
) -> bool:
    """Return whether one normalized range contains the full interval."""
    range_index = bisect_right(range_starts, start) - 1
    if range_index < 0:
        return False
    range_start, range_end = ranges[range_index]
    return range_start <= start and end <= range_end


def _validated_baseline_insertion_position(
    reference: object,
    baseline_lines: Sequence[bytes],
) -> int | None:
    """Return a structurally valid insertion position, or fail closed."""
    if not isinstance(reference, BaselineReference):
        return None
    if (
        type(reference.has_after_line) is not bool
        or not reference.has_after_line
        or type(reference.has_before_line) is not bool
    ):
        return None

    after_line = reference.after_line
    if after_line is not None and (
        type(after_line) is not int or after_line < 1
    ):
        return None
    if after_line is None:
        if reference.after_content is not None:
            return None
    elif not isinstance(reference.after_content, bytes):
        return None

    before_line = reference.before_line
    if reference.has_before_line:
        if before_line is not None and (
            type(before_line) is not int or before_line < 1
        ):
            return None
        if before_line is None:
            if reference.before_content is not None:
                return None
        elif not isinstance(reference.before_content, bytes):
            return None
    elif before_line is not None or reference.before_content is not None:
        return None

    try:
        position = _find_baseline_insertion_position(
            reference,
            baseline_lines,
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None
    if position is None:
        return None

    expected_before_line = (
        position + 1 if position < len(baseline_lines) else None
    )
    if reference.has_before_line and before_line != expected_before_line:
        return None
    return position


def _source_run_matches_insertion_boundary(
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    source_start: int,
    source_end: int,
    baseline_position: int,
) -> bool:
    """Return whether a full source run occupies one baseline boundary."""
    if baseline_position == 0:
        if source_start != 1:
            return False
    elif (
        source_start == 1
        or source_lines[source_start - 2]
        != baseline_lines[baseline_position - 1]
    ):
        return False

    if baseline_position == len(baseline_lines):
        return source_end == len(source_lines)
    return (
        source_end < len(source_lines)
        and source_lines[source_end] == baseline_lines[baseline_position]
    )


def _source_run_is_coherent_in_working(
    source_to_working_mapping: LineMapping,
    source_line_count: int,
    source_start: int,
    source_end: int,
) -> bool:
    """Return whether a source run and its context survive contiguously."""
    target_start = source_to_working_mapping.get_target_line_from_source_line(
        source_start
    )
    if target_start is None:
        return False

    for offset, source_line in enumerate(range(source_start, source_end + 1)):
        if source_to_working_mapping.get_target_line_from_source_line(
            source_line
        ) != target_start + offset:
            return False

    if source_start > 1 and (
        source_to_working_mapping.get_target_line_from_source_line(
            source_start - 1
        )
        != target_start - 1
    ):
        return False
    if source_end < source_line_count and (
        source_to_working_mapping.get_target_line_from_source_line(
            source_end + 1
        )
        != target_start + source_end - source_start + 1
    ):
        return False
    return True


def _source_run_has_distinctive_working_placement(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    source_to_working_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    working_occurrences: LinePayloadOccurrenceIndex,
    source_start: int,
    source_end: int,
) -> bool:
    """Return whether an insertion run is tied to one working realization."""
    target_start = source_to_working_mapping.get_target_line_from_source_line(
        source_start
    )
    target_end = source_to_working_mapping.get_target_line_from_source_line(
        source_end
    )
    if target_start is None or target_end is None:
        return False

    if source_start == 1 and target_start == 1:
        return True
    if source_end == len(source_lines) and target_end == len(working_lines):
        return True

    context_lines = []
    if source_start > 1:
        context_lines.append(source_start - 1)
    if source_end < len(source_lines):
        context_lines.append(source_end + 1)
    return any(
        source_occurrences.occurrence_count(source_lines[source_line - 1]) == 1
        and working_occurrences.occurrence_count(
            source_lines[source_line - 1]
        ) == 1
        for source_line in context_lines
    )


def _append_legacy_replacement_presence_ranges(
    replacement_ranges: list[tuple[int, int]],
    presence_ranges: Sequence[tuple[int, int]],
    presence_range_starts: Sequence[int],
    deletion_claims: Sequence[AbsenceClaim],
) -> None:
    """Exclude presence coupled to legacy immediate-source deletions."""
    for claim in deletion_claims:
        if not claim.content_lines:
            continue
        anchor_line = claim.anchor_line
        if anchor_line is None:
            replacement_start = 1
        elif type(anchor_line) is int and anchor_line >= 0:
            replacement_start = anchor_line + 1
        else:
            continue

        range_index = bisect_right(
            presence_range_starts,
            replacement_start,
        ) - 1
        if range_index < 0:
            continue
        presence_start, presence_end = presence_ranges[range_index]
        if presence_start <= replacement_start <= presence_end:
            replacement_ranges.append((replacement_start, presence_end))


def _append_pure_insertion_anchor_pairs(
    workspace: MatcherWorkspace,
    anchor_pairs: MappedRecordVector,
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    replacement_presence_lines: LineRanges,
    source_to_working_mapping: LineMapping | None,
    working_lines: Sequence[bytes] | None,
) -> None:
    """Append anchors around complete, live independent insertion runs."""
    if source_to_working_mapping is None or working_lines is None:
        return

    independent_presence_lines = presence_lines.difference(
        replacement_presence_lines
    )
    if not independent_presence_lines:
        return

    # An insertion anchor deliberately makes an otherwise equal source copy
    # unmatched. Require the complete source realization to survive so a copy
    # from an already-discarded baseline cannot impersonate the removed run.
    if any(
        not source_to_working_mapping.is_source_line_present(source_line)
        for source_line in range(1, len(source_lines) + 1)
    ):
        return

    references = EffectivePresenceReferenceIndex(workspace, ownership)
    positioned_lines = workspace.record_vector(len(references), "QQ")
    source_occurrences: LinePayloadOccurrenceIndex | None = None
    working_occurrences: LinePayloadOccurrenceIndex | None = None
    try:
        for source_line, reference in references.items():
            if (
                source_line > len(source_lines)
                or source_line not in independent_presence_lines
            ):
                continue
            baseline_position = _validated_baseline_insertion_position(
                reference,
                baseline_lines,
            )
            if baseline_position is None:
                continue
            positioned_lines.append((source_line, baseline_position))

        sort_mapped_records(positioned_lines)
        group_start = 0
        while group_start < len(positioned_lines):
            source_start, baseline_position = positioned_lines[group_start]
            source_end = source_start
            group_stop = group_start + 1
            while group_stop < len(positioned_lines):
                next_source_line, next_position = positioned_lines[group_stop]
                if (
                    next_source_line != source_end + 1
                    or next_position != baseline_position
                ):
                    break
                source_end = next_source_line
                group_stop += 1

            full_presence_run = (
                source_start - 1 not in presence_lines
                and source_end + 1 not in presence_lines
            )
            run_is_live = (
                full_presence_run
                and _source_run_matches_insertion_boundary(
                    source_lines,
                    baseline_lines,
                    source_start,
                    source_end,
                    baseline_position,
                )
                and _source_run_is_coherent_in_working(
                    source_to_working_mapping,
                    len(source_lines),
                    source_start,
                    source_end,
                )
            )
            if run_is_live:
                if source_occurrences is None or working_occurrences is None:
                    source_occurrences = LinePayloadOccurrenceIndex(
                        workspace,
                        source_lines,
                        normalize_payloads=False,
                    )
                    working_occurrences = LinePayloadOccurrenceIndex(
                        workspace,
                        working_lines,
                        normalize_payloads=False,
                    )
                if not _source_run_has_distinctive_working_placement(
                    source_lines,
                    working_lines,
                    source_to_working_mapping,
                    source_occurrences,
                    working_occurrences,
                    source_start,
                    source_end,
                ):
                    raise _MergeError(
                        _("Batch was created from a different version of the file")
                    )
                if baseline_position > 0:
                    anchor_pairs.append((baseline_position, source_start - 1))
                if baseline_position < len(baseline_lines):
                    anchor_pairs.append((baseline_position + 1, source_end + 1))

            group_start = group_stop
    finally:
        workspace.close_resource(positioned_lines)


@contextmanager
def acquire_deletion_anchor_pairs_for_target(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    *,
    trust_baseline_coordinates: bool = False,
    spool_dir: str | Path | None = None,
) -> Iterator[Sequence[tuple[int, int]]]:
    """Return coherent source-to-target anchors recorded by deletion claims.

    Exact baseline realization may trust recorded coordinates after checking
    the deleted bytes. Live targets additionally require the stored boundary
    identity to match before a coordinate can constrain structural alignment.
    """
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        anchor_pairs = workspace.record_vector(
            len(deletion_claims),
            "QQ",
        )
        occurrence_index: LinePayloadOccurrenceIndex | None = None
        for claim in deletion_claims:
            source_line = claim.anchor_line
            reference = claim.baseline_reference
            target_line = None if reference is None else reference.after_line
            if type(source_line) is not int or type(target_line) is not int:
                continue
            if source_line < 1 or source_line > len(source_lines):
                continue
            if target_line < 1 or target_line > len(target_lines):
                continue
            if source_lines[source_line - 1] != target_lines[target_line - 1]:
                continue

            if trust_baseline_coordinates:
                forbidden_sequence = normalize_line_sequence_endings(
                    claim.content_lines
                )
                if not forbidden_sequence or not _line_slice_matches(
                    target_lines,
                    target_line,
                    forbidden_sequence,
                ):
                    continue
            else:
                removal_edit = baseline_removal_edit(claim, target_lines)
                if removal_edit is None:
                    continue
                if (
                    not _removal_boundary_is_fixed_to_file_edge(claim)
                    and occurrence_index is None
                ):
                    occurrence_index = LinePayloadOccurrenceIndex(
                        workspace,
                        target_lines,
                    )
                if not _live_removal_boundary_is_unique(
                    claim,
                    target_lines,
                    removal_edit[0],
                    occurrence_index,
                ):
                    continue

            anchor_pairs.append((source_line, target_line))

        _sort_and_validate_anchor_pairs(anchor_pairs)
        yield cast(Sequence[tuple[int, int]], anchor_pairs)


@contextmanager
def acquire_discard_baseline_anchor_pairs(
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    ownership: BatchOwnership,
    *,
    source_to_working_mapping: LineMapping | None = None,
    working_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> Iterator[Sequence[tuple[int, int]]]:
    """Return coherent baseline-to-source anchors for discard alignment.

    Deletion references prove equal lines immediately before removed baseline
    content. A simple replacement unit can additionally prove the equal line
    immediately after that content. Complete independent insertion runs can
    prove either surrounding edge when their source context remains live.
    Validating every edge as one ordered set prevents locally plausible
    metadata from constraining a collectively inconsistent alignment.
    """
    deletion_claims = ownership.deletions
    replacement_units = ownership.replacement_units
    presence_lines = ownership.presence_line_set()
    presence_ranges = presence_lines.ranges()
    presence_range_starts = tuple(start for start, _end in presence_ranges)
    presence_reference_count = sum(
        len(claim.baseline_references)
        for claim in ownership.presence_claims
        if isinstance(claim.baseline_references, dict)
    )

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        anchor_pairs = workspace.record_vector(
            len(deletion_claims)
            + len(replacement_units)
            + 2 * presence_reference_count,
            "QQ",
        )
        replacement_presence_ranges: list[tuple[int, int]] = []
        replacement_ranges_are_valid = True

        for claim in deletion_claims:
            source_line = claim.anchor_line
            reference = claim.baseline_reference
            baseline_line = (
                None
                if (
                    not isinstance(reference, BaselineReference)
                    or type(reference.has_after_line) is not bool
                    or not reference.has_after_line
                )
                else reference.after_line
            )
            if type(source_line) is not int or type(baseline_line) is not int:
                continue
            if source_line < 1 or source_line > len(source_lines):
                continue
            if baseline_line < 1 or baseline_line > len(baseline_lines):
                continue
            if source_lines[source_line - 1] != baseline_lines[baseline_line - 1]:
                continue

            deleted_sequence = normalize_line_sequence_endings(claim.content_lines)
            if not deleted_sequence or not _line_slice_matches(
                baseline_lines,
                baseline_line,
                deleted_sequence,
            ):
                continue
            anchor_pairs.append((baseline_line, source_line))

        for unit in replacement_units:
            source_ranges = _collect_replacement_source_ranges(
                workspace,
                unit.presence_lines,
            )
            if source_ranges is None:
                replacement_ranges_are_valid = False
                continue
            try:
                replacement_presence_ranges.extend(
                    (source_start, source_end)
                    for source_start, source_end in source_ranges
                )
                if len(source_ranges) != 1 or len(unit.deletion_indices) != 1:
                    continue
                source_start, source_end = source_ranges[0]
                if (
                    source_start < 1
                    or source_end < source_start
                    or source_end >= len(source_lines)
                    or source_end + 1 in presence_lines
                    or not _line_range_is_contained(
                        presence_ranges,
                        presence_range_starts,
                        source_start,
                        source_end,
                    )
                ):
                    continue

                deletion_index = unit.deletion_indices[0]
                if (
                    type(deletion_index) is not int
                    or deletion_index < 0
                    or deletion_index >= len(deletion_claims)
                ):
                    continue
                claim = deletion_claims[deletion_index]
                if (
                    unit.origin is not None
                    and not _replacement_counts_cover_origin(
                        unit.origin,
                        source_end - source_start + 1,
                        len(claim.content_lines),
                    )
                ):
                    continue
                anchor_line = claim.anchor_line
                if anchor_line is not None and (
                    type(anchor_line) is not int or anchor_line < 1
                ):
                    continue
                if source_start != (anchor_line or 0) + 1:
                    continue

                reference = claim.baseline_reference
                if (
                    not isinstance(reference, BaselineReference)
                    or type(reference.has_after_line) is not bool
                    or not reference.has_after_line
                    or type(reference.has_before_line) is not bool
                    or not reference.has_before_line
                    or type(reference.before_line) is not int
                ):
                    continue
                after_line = reference.after_line
                if after_line is not None and (
                    type(after_line) is not int or after_line < 1
                ):
                    continue

                deleted_sequence = normalize_line_sequence_endings(
                    claim.content_lines
                )
                baseline_position = after_line or 0
                before_line = reference.before_line
                if (
                    not deleted_sequence
                    or baseline_position < 0
                    or before_line
                    != baseline_position + len(deleted_sequence) + 1
                    or before_line > len(baseline_lines)
                    or not _line_slice_matches(
                        baseline_lines,
                        baseline_position,
                        deleted_sequence,
                    )
                    or source_lines[source_end]
                    != baseline_lines[before_line - 1]
                ):
                    continue

                anchor_pairs.append((before_line, source_end + 1))
            finally:
                workspace.close_resource(source_ranges)

        if replacement_ranges_are_valid:
            _append_legacy_replacement_presence_ranges(
                replacement_presence_ranges,
                presence_ranges,
                presence_range_starts,
                deletion_claims,
            )
            _append_pure_insertion_anchor_pairs(
                workspace,
                anchor_pairs,
                source_lines,
                baseline_lines,
                ownership,
                presence_lines,
                LineRanges.from_ranges(replacement_presence_ranges),
                source_to_working_mapping,
                working_lines,
            )

        if not _sort_and_validate_anchor_pairs(anchor_pairs):
            raise _MergeError(
                _("Batch was created from a different version of the file")
            )
        yield cast(Sequence[tuple[int, int]], anchor_pairs)


def _removal_boundary_is_fixed_to_file_edge(claim: AbsenceClaim) -> bool:
    """Return whether a boundary marker admits only one target position."""
    reference = claim.baseline_reference
    return reference is not None and (
        reference.after_line is None
        or (
            reference.has_before_line
            and reference.before_line is None
        )
    )


def baseline_removal_edit(
    claim: AbsenceClaim,
    working_lines: Sequence[bytes],
) -> BaselineRemovalEdit | None:
    """Return the verified target span recorded by a removal claim."""
    if not claim.content_lines:
        return None

    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    position = _find_baseline_absence_position(
        claim.baseline_reference,
        working_lines,
        len(forbidden_sequence),
    )
    if position is None:
        return None
    if not _line_slice_matches(working_lines, position, forbidden_sequence):
        return None
    return position, position + len(forbidden_sequence)


def _removal_boundary_identity_matches_at(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    position: int,
    forbidden_sequence: Sequence[bytes],
) -> bool:
    """Return whether one target position has the claim's full identity."""
    reference = claim.baseline_reference
    if reference is None or not reference.has_after_line:
        return False
    if not _line_slice_matches(target_lines, position, forbidden_sequence):
        return False

    if reference.after_line is None:
        if position != 0:
            return False
    else:
        if position == 0 or reference.after_content is None:
            return False
        if _reference_line_payload(target_lines[position - 1]) != (
            _reference_line_payload(reference.after_content)
        ):
            return False

    if not reference.has_before_line:
        return True

    before_position = position + len(forbidden_sequence)
    if reference.before_line is None:
        return before_position == len(target_lines)
    if before_position >= len(target_lines) or reference.before_content is None:
        return False
    return _reference_line_payload(target_lines[before_position]) == (
        _reference_line_payload(reference.before_content)
    )


def _boundary_identity_occurs_once(
    occurrence_index: LinePayloadOccurrenceIndex,
    expected_position: int,
    last_position: int,
    *,
    after_content: bytes | None,
    span_contents: Sequence[bytes],
    before_content: bytes | None,
    before_delta: int,
    identity_matches_at: Callable[[int], bool],
    candidate_limit: int | None = _BOUNDARY_IDENTITY_CANDIDATE_LIMIT,
) -> bool:
    """Return whether indexed boundary content identifies one full identity."""
    rarest_content: bytes | None = None
    rarest_boundary_delta = 0
    rarest_count: int | None = None

    def consider(content: bytes | None, boundary_delta: int) -> None:
        nonlocal rarest_content
        nonlocal rarest_boundary_delta
        nonlocal rarest_count
        if content is None:
            return
        count = occurrence_index.occurrence_count(content)
        if rarest_count is None or count < rarest_count:
            rarest_content = content
            rarest_boundary_delta = boundary_delta
            rarest_count = count

    consider(after_content, 1)
    for offset, content in enumerate(span_contents):
        consider(content, -offset)
    consider(before_content, before_delta)

    if rarest_content is None or rarest_count is None or rarest_count == 0:
        return False
    if rarest_count == 1:
        return True

    for line_index in occurrence_index.matching_line_indexes(rarest_content):
        position = line_index + rarest_boundary_delta
        if (
            position < 0
            or position > last_position
            or position == expected_position
        ):
            continue
        if identity_matches_at(position):
            return False
    return True


def _live_removal_boundary_is_unique(
    claim: AbsenceClaim,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex | None,
) -> bool:
    """Require a live deletion boundary to identify one target occurrence."""
    forbidden_sequence = normalize_line_sequence_endings(claim.content_lines)
    if not forbidden_sequence or not _removal_boundary_identity_matches_at(
        claim,
        target_lines,
        expected_position,
        forbidden_sequence,
    ):
        return False
    if _removal_boundary_is_fixed_to_file_edge(claim):
        return True
    if occurrence_index is None:
        return False

    reference = claim.baseline_reference
    assert reference is not None
    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - len(forbidden_sequence),
        after_content=(
            reference.after_content if reference.after_line is not None else None
        ),
        span_contents=forbidden_sequence,
        before_content=(
            reference.before_content
            if reference.has_before_line and reference.before_line is not None
            else None
        ),
        before_delta=-len(forbidden_sequence),
        identity_matches_at=lambda position: _removal_boundary_identity_matches_at(
            claim,
            target_lines,
            position,
            forbidden_sequence,
        ),
    )


def _insertion_boundary_identity_matches_at(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one insertion position has the reference's full identity."""
    if reference is None or not reference.has_after_line:
        return False

    after_line = reference.after_line
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = reference.after_content
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if reference.has_before_line:
        before_line = reference.before_line
        if before_line is None:
            return position == len(target_lines)
        before_content = reference.before_content
        return (
            position < len(target_lines)
            and before_content is not None
            and _reference_line_payload(target_lines[position])
            == _reference_line_payload(before_content)
        )

    return position == len(target_lines)


def unique_live_insertion_boundary_position(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    occurrence_index: LinePayloadOccurrenceIndex | None,
) -> int | None:
    """Return a shifted insertion boundary with one unique boundary line.

    File-edge references are intrinsically positioned.  Interior references
    are relocated only when either saved neighbor occurs once in the target;
    the other neighbor must still complete the saved boundary identity.  This
    keeps repeated-reference planning linear after one storage-backed index is
    built instead of scanning every occurrence for every claimed line.
    """
    if reference is None or not reference.has_after_line:
        return None

    if reference.after_line is None:
        position = 0
    elif not reference.has_before_line or reference.before_line is None:
        position = len(target_lines)
    else:
        if occurrence_index is None:
            return None
        candidates = (
            (reference.after_content, 1),
            (reference.before_content, 0),
        )
        unique_content: bytes | None = None
        boundary_delta = 0
        for content, delta in candidates:
            if (
                content is not None
                and occurrence_index.occurrence_count(content) == 1
            ):
                unique_content = content
                boundary_delta = delta
                break
        if unique_content is None:
            return None
        line_index = next(
            occurrence_index.matching_line_indexes(unique_content),
            None,
        )
        if line_index is None:
            return None
        position = line_index + boundary_delta

    if _insertion_boundary_identity_matches_at(
        reference,
        target_lines,
        position,
    ):
        return position
    return None


def _live_insertion_boundary_is_unique(
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require an insertion reference to identify one live target boundary."""
    if not _insertion_boundary_identity_matches_at(
        reference,
        target_lines,
        expected_position,
    ):
        return False

    assert reference is not None
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines),
        after_content=(
            reference.after_content
            if after_line is not None
            else None
        ),
        span_contents=(),
        before_content=(
            reference.before_content
            if (
                reference.has_before_line and before_line is not None
            )
            else None
        ),
        before_delta=0,
        identity_matches_at=lambda position: _insertion_boundary_identity_matches_at(
            reference,
            target_lines,
            position,
        ),
        candidate_limit=None,
    )


def _source_mapping_identifies_missing_insertion_run(
    mapping: LineMapping,
    reference: BaselineReference | None,
    target_lines: Sequence[bytes],
    run_start: int,
    run_end: int,
    position: int,
) -> bool:
    """Return whether a mapped predecessor proves one missing run's boundary."""
    source_line_count = len(mapping.source_to_target)
    if (
        run_start <= 1
        or run_end < run_start
        or run_end > source_line_count
        or not _insertion_boundary_identity_matches_at(
            reference,
            target_lines,
            position,
        )
    ):
        return False
    for source_line in range(run_start, run_end + 1):
        if mapping.get_target_line_from_source_line(source_line) is not None:
            return False

    return (
        mapping.get_target_line_from_source_line(run_start - 1)
        == position
    )


def _replacement_origin_boundary_identity_matches_at(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    position: int,
) -> bool:
    """Return whether one target span has the replacement parent's identity."""
    if origin is None:
        return False
    reference = origin.baseline_reference
    old_line_count = origin.old_line_count
    if (
        reference is None
        or not reference.has_after_line
        or old_line_count <= 0
        or position < 0
        or position + old_line_count > len(target_lines)
    ):
        return False

    after_line = reference.after_line
    if after_line is None:
        if position != 0:
            return False
    else:
        after_content = reference.after_content
        if (
            position == 0
            or after_content is None
            or _reference_line_payload(target_lines[position - 1])
            != _reference_line_payload(after_content)
        ):
            return False

    if not reference.has_before_line:
        return True

    before_line = reference.before_line
    before_position = position + old_line_count
    if before_line is None:
        return before_position == len(target_lines)
    before_content = reference.before_content
    return (
        before_position < len(target_lines)
        and before_content is not None
        and _reference_line_payload(target_lines[before_position])
        == _reference_line_payload(before_content)
    )


def _live_replacement_origin_boundary_is_unique(
    origin: ReplacementUnitOrigin | None,
    target_lines: Sequence[bytes],
    expected_position: int,
    occurrence_index: LinePayloadOccurrenceIndex,
) -> bool:
    """Require a replacement parent to identify one live target span."""
    if origin is None or not _replacement_origin_boundary_identity_matches_at(
        origin,
        target_lines,
        expected_position,
    ):
        return False

    reference = origin.baseline_reference
    if reference is None:
        return False
    after_line = reference.after_line
    before_line = reference.before_line
    if after_line is None or (
        reference.has_before_line
        and before_line is None
    ):
        return True

    return _boundary_identity_occurs_once(
        occurrence_index,
        expected_position,
        len(target_lines) - origin.old_line_count,
        after_content=(reference.after_content if after_line is not None else None),
        span_contents=(),
        before_content=(
            reference.before_content
            if reference.has_before_line and before_line is not None
            else None
        ),
        before_delta=-origin.old_line_count,
        identity_matches_at=lambda position: (
            _replacement_origin_boundary_identity_matches_at(
                origin,
                target_lines,
                position,
            )
        ),
    )


def live_coordinate_edits_are_safe(
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: Sequence[tuple[int, ...]],
    positioned_insertion_lines: Sequence[tuple[int, ...]] | None,
    *,
    spool_dir: str | Path | None,
) -> bool:
    """Return whether every coordinate edit has one live target boundary."""
    if not deletion_claims and not positioned_insertion_lines:
        return True

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        occurrence_index = LinePayloadOccurrenceIndex(
            workspace,
            working_lines,
        )
        replacement_reference_count = sum(
            len(unit.deletion_indices)
            for unit in ownership.replacement_units
        )
        replacement_units_by_deletion = workspace.record_vector(
            replacement_reference_count,
            "QQ",
        )
        for unit_index, unit in enumerate(ownership.replacement_units):
            for deletion_index in unit.deletion_indices:
                if (
                    type(deletion_index) is int
                    and 0 <= deletion_index < len(deletion_claims)
                ):
                    replacement_units_by_deletion.append((
                        deletion_index,
                        unit_index,
                    ))
        sort_mapped_records(replacement_units_by_deletion)

        replacement_reference_index = 0
        for deletion_index, claim in enumerate(deletion_claims):
            (
                has_actual_bounds,
                actual_start,
                actual_end,
                coordinate_was_reviewed,
            ) = (
                deletion_edit_bounds[deletion_index]
            )
            if not has_actual_bounds:
                return False
            actual_bounds = (actual_start, actual_end)
            while (
                replacement_reference_index
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    replacement_reference_index
                ][0] < deletion_index
            ):
                replacement_reference_index += 1
            next_replacement_reference = replacement_reference_index
            while (
                next_replacement_reference
                < len(replacement_units_by_deletion)
                and replacement_units_by_deletion[
                    next_replacement_reference
                ][0] == deletion_index
            ):
                next_replacement_reference += 1
            if coordinate_was_reviewed:
                replacement_reference_index = next_replacement_reference
                continue

            removal_edit = baseline_removal_edit(claim, working_lines)
            if (
                removal_edit is not None
                and actual_bounds == removal_edit[:2]
                and _live_removal_boundary_is_unique(
                    claim,
                    working_lines,
                    removal_edit[0],
                    occurrence_index,
                )
            ):
                replacement_reference_index = next_replacement_reference
                continue

            origin_is_safe = False
            for reference_index in range(
                replacement_reference_index,
                next_replacement_reference,
            ):
                unit_index = replacement_units_by_deletion[reference_index][1]
                unit = ownership.replacement_units[unit_index]
                origin = getattr(unit, "origin", None)
                parent_bounds = replacement_origin_absence_bounds(
                    origin,
                    working_lines,
                )
                if (
                    parent_bounds is not None
                    and parent_bounds[0] <= actual_bounds[0]
                    and actual_bounds[1] <= parent_bounds[1]
                    and _live_replacement_origin_boundary_is_unique(
                        origin,
                        working_lines,
                        parent_bounds[0],
                        occurrence_index,
                    )
                ):
                    origin_is_safe = True
                    break
            if not origin_is_safe:
                return False
            replacement_reference_index = next_replacement_reference

        if positioned_insertion_lines is not None:
            for position, claimed_line in positioned_insertion_lines:
                reference = ownership.presence_baseline_reference(
                    claimed_line
                )
                if not _live_insertion_boundary_is_unique(
                    reference,
                    working_lines,
                    position,
                    occurrence_index,
                ):
                    return False

    return True


def replacement_origin_absence_bounds(
    origin: ReplacementUnitOrigin | None,
    working_lines: Sequence[bytes],
) -> tuple[int, int] | None:
    """Return the target bounds of an original replacement parent, if provable."""
    if origin is None or origin.baseline_reference is None:
        return None
    old_line_count = origin.old_line_count
    if old_line_count <= 0:
        return None

    position = _find_baseline_absence_position(
        origin.baseline_reference,
        working_lines,
        old_line_count,
    )
    if position is None:
        return None
    return position, position + old_line_count
