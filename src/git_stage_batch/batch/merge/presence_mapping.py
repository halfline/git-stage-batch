"""Map lines without letting selected text displace unselected text."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
import sys
from typing import TYPE_CHECKING, overload

from ...core.line_selection import (
    LineRangeBuilder,
    LineRanges,
    sorted_line_ranges_contain,
)
from ...core.mapped_storage import (
    MappedIntVector,
    MappedRecordVector,
    sort_mapped_records,
)
from ...core.resource_cleanup import close_resources_preserving_first
from .baseline_anchor_matching import unique_live_insertion_boundary_position
from .presence_reference_index import EffectivePresenceReferenceIndex
from ..line_matching.line_mapping import LineMapping, allocate_line_mapping
from ..line_matching.match import match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)
from ..ownership.claims import parse_ownership_line_ranges

if TYPE_CHECKING:
    from ..ownership.absence_claims import AbsenceClaim
    from ..ownership.model import BatchOwnership
    from ..ownership.references import BaselineReference
    from ..ownership.resolved_replacement_alternatives import (
        ResolvedReplacementAlternative,
    )


_CONTROLLED_SOURCE_LINE = object()
_RECORDED_PRESENCE_BOUNDARY_FORMAT = "QQQ"
_REPLACEMENT_ALTERNATIVE_LIVE_RUN_FORMAT = "QQQ"


class PresenceMappingCorrection(Enum):
    """Whether the usual line mapping had to be changed."""

    ORDINARY = auto()
    CORRECTED = auto()


class PresenceMappingAmbiguity(Enum):
    """Whether another mapping could also be valid."""

    NONE = auto()
    UNRESOLVED = auto()
    COMPETING_CONTEXT = auto()


@dataclass(frozen=True, slots=True)
class PresenceMappingResult:
    """One mapping plus its ownership and context-correction state."""

    mapping: LineMapping
    owned: bool
    correction: PresenceMappingCorrection
    ambiguity: PresenceMappingAmbiguity

    @property
    def corrected(self) -> bool:
        """Return whether the usual mapping was changed."""
        return self.correction is PresenceMappingCorrection.CORRECTED

    @property
    def ambiguous(self) -> bool:
        """Return whether placement is still ambiguous."""
        return self.ambiguity is not PresenceMappingAmbiguity.NONE

    @property
    def competing_context(self) -> bool:
        """Return whether another valid mapping puts lines elsewhere."""
        return self.ambiguity is PresenceMappingAmbiguity.COMPETING_CONTEXT


def _presence_mapping_ambiguity(
    *,
    ambiguous: bool,
    competing_context: bool,
) -> PresenceMappingAmbiguity:
    """Choose the ambiguity state for these results."""
    if competing_context:
        if not ambiguous:
            raise ValueError("competing context requires an ambiguous mapping")
        return PresenceMappingAmbiguity.COMPETING_CONTEXT
    if ambiguous:
        return PresenceMappingAmbiguity.UNRESOLVED
    return PresenceMappingAmbiguity.NONE


@dataclass(frozen=True, slots=True)
class _PresenceBoundaryEvidence:
    """Storage-backed referenced runs and uniquely proven live boundaries."""

    referenced_runs: MappedRecordVector
    unique_boundaries: MappedRecordVector
    coordinate_boundaries: MappedRecordVector


@dataclass(slots=True)
class _WorkspaceExitState:
    """Record whether a matcher workspace completed its own cleanup."""

    completed: bool = False


@contextmanager
def _tracked_matcher_workspace(
    state: _WorkspaceExitState,
    *,
    spool_dir: str | Path | None,
) -> Iterator[MatcherWorkspace]:
    """Expose a workspace and mark only a fully successful context exit."""
    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        yield workspace
    state.completed = True


class _UncontrolledSourceView(Sequence[Hashable]):
    """Lazy source view that hides batch-controlled lines from matching."""

    def __init__(
        self,
        source_lines: Sequence[bytes],
        controlled_lines: LineRanges,
        indices: range | None = None,
    ) -> None:
        self._source_lines = source_lines
        self._controlled_lines = controlled_lines
        self._indices = range(len(source_lines)) if indices is None else indices

    def __len__(self) -> int:
        return len(self._indices)

    @overload
    def __getitem__(self, index: int) -> Hashable: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Hashable]: ...

    def __getitem__(self, index: int | slice) -> Hashable | Sequence[Hashable]:
        if isinstance(index, slice):
            return _UncontrolledSourceView(
                self._source_lines,
                self._controlled_lines,
                self._indices[index],
            )
        try:
            source_index = self._indices[index]
        except IndexError as error:
            raise IndexError(index) from error
        if source_index + 1 in self._controlled_lines:
            return _CONTROLLED_SOURCE_LINE
        return self._source_lines[source_index]


def presence_lines_requiring_context_protection(
    ownership: BatchOwnership,
    presence_lines: LineRanges,
    deletion_claims: Sequence[AbsenceClaim],
) -> LineRanges:
    """Return presence whose duplicates need insertion-style protection.

    Ordinary replacement new sides already have an explicitly coupled old side.
    Their placement is validated by the replacement planner, so hiding them from
    structural matching can only discard useful evidence. Independent presence
    and explicit source-alternative replacements do need protection: otherwise a
    selected duplicate can consume live context before those claims are placed.
    """
    ordinary_replacement_lines = LineRangeBuilder()
    for unit in ownership.replacement_units:
        deletion_indices = unit.deletion_indices
        if not deletion_indices or any(
            type(deletion_index) is not int
            or deletion_index < 0
            or deletion_index >= len(deletion_claims)
            for deletion_index in deletion_indices
        ):
            continue
        if any(
            deletion_claims[deletion_index].source_alternative
            for deletion_index in deletion_indices
        ):
            continue
        for range_start, range_end in parse_ownership_line_ranges(
            unit.presence_lines
        ).ranges():
            ordinary_replacement_lines.add_range(range_start, range_end)
    return presence_lines.difference(ordinary_replacement_lines.finish())


def match_uncontrolled_context_lines(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    *,
    spool_dir: str | Path | None = None,
    matcher: Callable[..., LineMapping] = match_lines,
) -> LineMapping:
    """Map only unowned context while hiding controlled source payloads."""
    return matcher(
        _UncontrolledSourceView(source_lines, controlled_source_lines),
        target_lines,
        spool_dir=spool_dir,
    )


def _source_run_matches_recorded_boundary(
    source_lines: Sequence[bytes],
    source_start: int,
    source_end: int,
    reference: BaselineReference,
) -> bool:
    """Return whether one saved boundary encloses the complete source run."""
    if not reference.has_after_line:
        return False
    if reference.after_line is None:
        if source_start != 1 or reference.after_content is not None:
            return False
    elif (
        source_start == 1
        or reference.after_content is None
        or normalized_line_payload(source_lines[source_start - 2])
        != normalized_line_payload(reference.after_content)
    ):
        return False

    if not reference.has_before_line:
        return source_end == len(source_lines)
    if reference.before_line is None:
        return source_end == len(source_lines) and reference.before_content is None
    return (
        source_end < len(source_lines)
        and reference.before_content is not None
        and normalized_line_payload(source_lines[source_end])
        == normalized_line_payload(reference.before_content)
    )


def _presence_boundary_evidence(
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    references: EffectivePresenceReferenceIndex | None,
    presence_lines: LineRanges,
) -> _PresenceBoundaryEvidence:
    """Return complete referenced runs and their unique live boundaries."""
    presence_ranges = presence_lines.ranges()
    referenced_runs = workspace.record_vector(len(presence_ranges), "QQ")
    boundaries = workspace.record_vector(
        len(presence_ranges),
        _RECORDED_PRESENCE_BOUNDARY_FORMAT,
    )
    coordinate_boundaries = workspace.record_vector(
        len(presence_ranges),
        _RECORDED_PRESENCE_BOUNDARY_FORMAT,
    )
    if references is None or not presence_lines:
        return _PresenceBoundaryEvidence(
            referenced_runs,
            boundaries,
            coordinate_boundaries,
        )

    occurrence_index: LinePayloadOccurrenceIndex | None = None
    try:
        for source_start, source_end in presence_ranges:
            if source_start < 1 or source_end > len(source_lines):
                continue
            reference = references.reference_for(source_start)
            if reference is None or any(
                references.reference_for(source_line) != reference
                for source_line in range(source_start + 1, source_end + 1)
            ):
                continue
            if (
                reference.after_line is not None
                and reference.has_before_line
                and reference.before_line is not None
                and occurrence_index is None
            ):
                occurrence_index = LinePayloadOccurrenceIndex(
                    workspace,
                    target_lines,
                )
            try:
                target_position = unique_live_insertion_boundary_position(
                    reference,
                    target_lines,
                    occurrence_index,
                )
            except (AttributeError, IndexError, TypeError, ValueError):
                target_position = None
            if target_position is not None:
                coordinate_boundaries.append(
                    (source_start, source_end, target_position)
                )
            if not _source_run_matches_recorded_boundary(
                source_lines,
                source_start,
                source_end,
                reference,
            ):
                continue
            referenced_runs.append((source_start, source_end))
            if target_position is not None:
                boundaries.append((source_start, source_end, target_position))
    except BaseException:
        if occurrence_index is not None:
            try:
                occurrence_index.close()
            except BaseException:
                pass
        raise
    else:
        if occurrence_index is not None:
            occurrence_index.close()
    return _PresenceBoundaryEvidence(
        referenced_runs,
        boundaries,
        coordinate_boundaries,
    )


def _recorded_presence_ending_before(
    boundaries: Sequence[tuple[int, ...]],
    source_line: int,
    target_line: int,
) -> tuple[int, int] | None:
    """Return a recorded run immediately before one mapped context run."""
    desired_end = source_line - 1
    low = 0
    high = len(boundaries)
    while low < high:
        middle = (low + high) // 2
        if boundaries[middle][1] < desired_end:
            low = middle + 1
        else:
            high = middle
    if low >= len(boundaries):
        return None
    source_start, source_end, target_position = boundaries[low]
    if source_end + 1 == source_line and target_position + 1 == target_line:
        return source_start, source_end
    return None


def _recorded_presence_starting_after(
    boundaries: Sequence[tuple[int, ...]],
    source_line: int,
    target_line: int,
) -> tuple[int, int] | None:
    """Return a recorded run immediately after one mapped context run."""
    desired_start = source_line + 1
    low = 0
    high = len(boundaries)
    while low < high:
        middle = (low + high) // 2
        if boundaries[middle][0] < desired_start:
            low = middle + 1
        else:
            high = middle
    if low >= len(boundaries):
        return None
    source_start, source_end, target_position = boundaries[low]
    if source_start - 1 == source_line and target_position == target_line:
        return source_start, source_end
    return None


def _replacement_alternative_live_runs(
    workspace: MatcherWorkspace,
    alternatives: Sequence["ResolvedReplacementAlternative"],
) -> MappedRecordVector:
    """Record where each live version appears in the source."""
    live_runs = workspace.record_vector(
        sum(len(alternative.live_payload) for alternative in alternatives),
        _REPLACEMENT_ALTERNATIVE_LIVE_RUN_FORMAT,
    )
    for alternative_index, alternative in enumerate(alternatives):
        for payload_span in alternative.live_payload:
            live_runs.append(
                (
                    payload_span.start.offset,
                    payload_span.end.offset,
                    alternative_index,
                )
            )
    sort_mapped_records(live_runs)
    previous_end = 0
    for run_start, run_end, _alternative_index in live_runs:
        if run_start < previous_end:
            raise ValueError("replacement alternative live payloads overlap")
        previous_end = run_end
    return live_runs


def _complete_live_alternative_replaces_saved_line(
    alternatives: Sequence["ResolvedReplacementAlternative"],
    live_runs: Sequence[tuple[int, ...]],
    *,
    live_source_line: int,
    saved_source_line: int,
    live_run_start: int,
    live_run_end: int,
) -> bool:
    """Check whether this line belongs to the live version paired with it."""
    live_offset = live_source_line - 1
    lower = 0
    upper = len(live_runs)
    while lower < upper:
        middle = (lower + upper) // 2
        if live_runs[middle][0] <= live_offset:
            lower = middle + 1
        else:
            upper = middle
    live_run_index = lower - 1
    if live_run_index < 0:
        return False
    live_start, live_end, alternative_index = live_runs[live_run_index]
    if not live_start <= live_offset < live_end:
        return False
    alternative = alternatives[alternative_index]
    saved_offset = saved_source_line - 1
    return (
        alternative.saved.start.offset <= saved_offset < alternative.saved.end.offset
        and live_run_start - 1 <= alternative.live_envelope.start.offset
        and alternative.live_envelope.end.offset <= live_run_end
    )


def _authorized_context_corrections(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    preferred_context_lines: LineRanges,
    boundary_evidence: _PresenceBoundaryEvidence,
    ordinary_mapping: LineMapping,
    context_mapping: LineMapping,
    replacement_alternatives: Sequence["ResolvedReplacementAlternative"],
    replacement_alternative_live_runs: Sequence[tuple[int, ...]],
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    corrections: MappedRecordVector,
    ordinary_authorized_targets: MappedIntVector,
    context_authorized_targets: MappedIntVector,
) -> None:
    """Append context runs authorized to displace selected mappings."""
    preferred_context_ranges = preferred_context_lines.ranges()

    def append_run(
        source_start: int,
        source_end: int,
        target_start: int,
    ) -> None:
        run_is_distinctive = False
        for source_line in range(source_start, source_end + 1):
            target_line = target_start + source_line - source_start
            content = source_lines[source_line - 1]
            if (
                source_occurrences.occurrence_count(content) == 1
                and target_occurrences.occurrence_count(target_lines[target_line - 1])
                == 1
            ):
                run_is_distinctive = True
                break
        preceding_presence = _recorded_presence_ending_before(
            boundary_evidence.unique_boundaries,
            source_start,
            target_start,
        )
        following_presence = _recorded_presence_starting_after(
            boundary_evidence.unique_boundaries,
            source_end,
            target_start + source_end - source_start,
        )
        for source_line in range(source_start, source_end + 1):
            target_line = target_start + source_line - source_start
            ordinary_source = ordinary_mapping.get_source_line_from_target_line(
                target_line
            )
            has_complete_preferred_context = (
                _run_contains_complete_preferred_context_range(
                    preferred_context_ranges,
                    source_start,
                    source_end,
                    source_line,
                )
            )
            paired_replacement_alternative = (
                ordinary_source is not None
                and _complete_live_alternative_replaces_saved_line(
                    replacement_alternatives,
                    replacement_alternative_live_runs,
                    live_source_line=source_line,
                    saved_source_line=ordinary_source,
                    live_run_start=source_start,
                    live_run_end=source_end,
                )
            )
            if ordinary_source is None and has_complete_preferred_context:
                context_authorized_targets[target_line - 1] = 1
                corrections.append((source_line, target_line))
                continue
            ordinary_source_has_recorded_boundary = (
                ordinary_source is not None
                and sorted_line_ranges_contain(
                    boundary_evidence.referenced_runs,
                    ordinary_source,
                )
            )
            ordinary_source_has_recorded_coordinate = (
                ordinary_source is not None
                and sorted_line_ranges_contain(
                    boundary_evidence.coordinate_boundaries,
                    ordinary_source,
                )
            )
            explicitly_authorized = ordinary_source is not None and (
                has_complete_preferred_context
                or (
                    preceding_presence is not None
                    and preceding_presence[0]
                    <= ordinary_source
                    <= preceding_presence[1]
                )
                or (
                    following_presence is not None
                    and following_presence[0]
                    <= ordinary_source
                    <= following_presence[1]
                )
            )
            distinctively_authorized = (
                ordinary_source is not None
                and run_is_distinctive
                and not ordinary_source_has_recorded_boundary
            )
            context_line_is_distinctive = (
                source_occurrences.occurrence_count(source_lines[source_line - 1]) == 1
                and target_occurrences.occurrence_count(target_lines[target_line - 1])
                == 1
            )
            if (
                ordinary_source is None
                or ordinary_source == source_line
                or ordinary_source not in controlled_source_lines
                or not (explicitly_authorized or distinctively_authorized)
                or (
                    ordinary_authorized_targets[target_line - 1]
                    and ordinary_source_has_recorded_coordinate
                    and not context_line_is_distinctive
                    and not paired_replacement_alternative
                )
            ):
                continue
            context_authorized_targets[target_line - 1] = 1
            if (
                paired_replacement_alternative
                or not ordinary_authorized_targets[target_line - 1]
            ):
                corrections.append((source_line, target_line))

    run_source_start: int | None = None
    run_source_end = 0
    run_target_start = 0
    run_target_end = 0
    for source_line, target_line in context_mapping.mapped_line_pairs():
        if (
            run_source_start is not None
            and source_line == run_source_end + 1
            and target_line == run_target_end + 1
        ):
            run_source_end = source_line
            run_target_end = target_line
            continue
        if run_source_start is not None:
            append_run(run_source_start, run_source_end, run_target_start)
        run_source_start = source_line
        run_source_end = source_line
        run_target_start = target_line
        run_target_end = target_line
    if run_source_start is not None:
        append_run(run_source_start, run_source_end, run_target_start)


def _run_contains_complete_preferred_context_range(
    preferred_ranges: Sequence[tuple[int, int]],
    run_start: int,
    run_end: int,
    source_line: int,
) -> bool:
    """Return whether a mapped run contains this line's complete old side."""
    range_index = (
        bisect_right(
            preferred_ranges,
            (source_line, sys.maxsize),
        )
        - 1
    )
    if range_index < 0:
        return False
    preferred_start, preferred_end = preferred_ranges[range_index]
    return (
        preferred_start <= source_line <= preferred_end
        and run_start <= preferred_start
        and preferred_end <= run_end
    )


def _mark_distinctively_anchored_controlled_spans(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    authorized_targets: MappedIntVector,
) -> None:
    """Mark mapped selected runs whose placement has distinctive evidence."""

    run_source_end = 0
    run_target_start = 0
    run_target_end = 0
    run_is_distinctive = False

    def mark_run() -> None:
        if not run_is_distinctive:
            return
        for target_line in range(run_target_start, run_target_end + 1):
            authorized_targets[target_line - 1] = 1

    for source_line, target_line in ordinary_mapping.mapped_line_pairs():
        if source_line not in controlled_source_lines:
            mark_run()
            run_source_end = 0
            run_target_start = 0
            run_target_end = 0
            run_is_distinctive = False
            continue
        if run_source_end and (
            source_line != run_source_end + 1 or target_line != run_target_end + 1
        ):
            mark_run()
            run_target_start = 0
            run_is_distinctive = False
        if not run_target_start:
            run_target_start = target_line
        run_source_end = source_line
        run_target_end = target_line
        if (
            source_occurrences.occurrence_count(source_lines[source_line - 1]) == 1
            and target_occurrences.occurrence_count(target_lines[target_line - 1]) == 1
        ):
            run_is_distinctive = True
    mark_run()


def _mark_explicitly_anchored_controlled_lines(
    controlled_source_lines: LineRanges,
    anchor_authorized_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    anchor_pairs: Sequence[tuple[int, int]],
    authorized_targets: MappedIntVector,
) -> None:
    """Accept a repeated line when an exact anchor chooses this copy."""
    for source_line, target_line in anchor_pairs:
        if (
            source_line not in controlled_source_lines
            or source_line not in anchor_authorized_source_lines
            or target_line < 1
            or target_line > len(authorized_targets)
            or ordinary_mapping.get_target_line_from_source_line(source_line)
            != target_line
        ):
            continue
        authorized_targets[target_line - 1] = 1


def _append_context_for_incomplete_controlled_runs(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    context_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    references: EffectivePresenceReferenceIndex | None,
    corrections: MappedRecordVector,
    context_authorized_targets: MappedIntVector,
) -> None:
    """Keep adjacent context when a selected run is clearly incomplete."""

    def stable_neighbor(source_line: int, target_line: int) -> bool:
        content = source_lines[source_line - 1]
        return (
            ordinary_mapping.get_target_line_from_source_line(source_line)
            == target_line
            and source_occurrences.occurrence_count(content) == 1
            and target_occurrences.occurrence_count(content) == 1
        )

    for run_start, run_end in controlled_source_lines.ranges():
        if run_start < 1 or run_end > len(source_lines):
            continue
        first_reference = (
            None if references is None else references.reference_for(run_start)
        )
        has_any_reference = first_reference is not None
        has_consistent_references = True
        has_missing_distinctive_line = False
        for source_line in range(run_start, run_end + 1):
            if references is not None:
                reference = references.reference_for(source_line)
                has_any_reference = has_any_reference or reference is not None
                has_consistent_references = (
                    has_consistent_references and reference == first_reference
                )
            content = source_lines[source_line - 1]
            has_missing_distinctive_line = has_missing_distinctive_line or (
                ordinary_mapping.get_target_line_from_source_line(source_line) is None
                and source_occurrences.occurrence_count(content) == 1
                and target_occurrences.occurrence_count(content) == 0
            )
        if has_any_reference and (
            first_reference is None or not has_consistent_references
        ):
            continue
        if not has_missing_distinctive_line:
            continue

        before_source = run_start - 1 if run_start > 1 else None
        after_source = run_end + 1 if run_end < len(source_lines) else None
        before_target = (
            0
            if before_source is None
            else context_mapping.get_target_line_from_source_line(before_source)
        )
        after_target = (
            len(target_lines) + 1
            if after_source is None
            else context_mapping.get_target_line_from_source_line(after_source)
        )
        if (
            before_target is None
            or after_target is None
            or after_target != before_target + 1
        ):
            continue

        has_stable_neighbor = (
            before_source is not None
            and before_target > 0
            and stable_neighbor(before_source, before_target)
        ) or (
            after_source is not None
            and after_target <= len(target_lines)
            and stable_neighbor(after_source, after_target)
        )
        if not has_stable_neighbor:
            continue

        for context_source, context_target in (
            (before_source, before_target),
            (after_source, after_target),
        ):
            if (
                context_source is None
                or context_source in controlled_source_lines
                or not 1 <= context_target <= len(target_lines)
            ):
                continue
            ordinary_source = ordinary_mapping.get_source_line_from_target_line(
                context_target
            )
            if ordinary_source is None or not run_start <= ordinary_source <= run_end:
                continue
            context_authorized_targets[context_target - 1] = 1
            corrections.append((context_source, context_target))


def _append_distinctive_controlled_run_extensions(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    corrections: MappedRecordVector,
    authorized_targets: MappedIntVector,
) -> None:
    """Extend a verified run through repeated lines assigned to another copy."""
    for range_start, range_end in controlled_source_lines.ranges():
        previous_source = 0
        previous_target = 0
        run_is_distinctive = False
        for source_line in range(
            max(1, range_start),
            min(len(source_lines), range_end) + 1,
        ):
            target_line = ordinary_mapping.get_target_line_from_source_line(source_line)
            if target_line is not None:
                if (
                    source_line != previous_source + 1
                    or target_line != previous_target + 1
                ):
                    run_is_distinctive = False
                if (
                    source_occurrences.occurrence_count(source_lines[source_line - 1])
                    == 1
                    and target_occurrences.occurrence_count(
                        target_lines[target_line - 1]
                    )
                    == 1
                ):
                    run_is_distinctive = True
                previous_source = source_line
                previous_target = target_line
                continue

            candidate_target = previous_target + 1
            occupying_source = (
                ordinary_mapping.get_source_line_from_target_line(candidate_target)
                if candidate_target <= len(target_lines)
                else None
            )
            if (
                run_is_distinctive
                and previous_source == source_line - 1
                and occupying_source is not None
                and occupying_source not in controlled_source_lines
                and source_lines[source_line - 1] == target_lines[candidate_target - 1]
            ):
                corrections.append((source_line, candidate_target))
                authorized_targets[candidate_target - 1] = 1
                previous_source = source_line
                previous_target = candidate_target
                continue

            previous_source = 0
            previous_target = 0
            run_is_distinctive = False


def _append_local_context_extensions(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    corrections: MappedRecordVector,
) -> None:
    """Keep repeated lines next to the unique text that identifies them."""
    previous_source = 0
    previous_target = 0
    run_has_unique_line = False

    for source_line in range(1, len(source_lines) + 1):
        if source_line in controlled_source_lines:
            previous_source = 0
            previous_target = 0
            run_has_unique_line = False
            continue

        target_line = ordinary_mapping.get_target_line_from_source_line(source_line)
        if target_line is not None:
            if source_line != previous_source + 1 or target_line != previous_target + 1:
                run_has_unique_line = False
            if (
                source_occurrences.occurrence_count(source_lines[source_line - 1]) == 1
                and target_occurrences.occurrence_count(target_lines[target_line - 1])
                == 1
            ):
                run_has_unique_line = True
            previous_source = source_line
            previous_target = target_line
            continue

        candidate_target = previous_target + 1
        occupying_source = (
            ordinary_mapping.get_source_line_from_target_line(candidate_target)
            if candidate_target <= len(target_lines)
            else None
        )
        if (
            run_has_unique_line
            and previous_source == source_line - 1
            and occupying_source is not None
            and occupying_source > source_line
            and occupying_source not in controlled_source_lines
            and source_lines[source_line - 1] == target_lines[candidate_target - 1]
        ):
            corrections.append((source_line, candidate_target))
            previous_source = source_line
            previous_target = candidate_target
            continue

        previous_source = 0
        previous_target = 0
        run_has_unique_line = False


def _append_coordinate_bounded_controlled_run_extensions(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    ordinary_mapping: LineMapping,
    source_occurrences: LinePayloadOccurrenceIndex,
    target_occurrences: LinePayloadOccurrenceIndex,
    coordinate_boundaries: Sequence[tuple[int, ...]],
    corrections: MappedRecordVector,
    authorized_targets: MappedIntVector,
) -> None:
    """Move a selected suffix before the boundary saved with it."""
    for range_start, range_end, target_boundary in coordinate_boundaries:
        anchor_source = 0
        anchor_target = 0
        for source_line in range(range_start, range_end + 1):
            target_line = ordinary_mapping.get_target_line_from_source_line(source_line)
            if target_line is None or target_line >= target_boundary:
                continue
            content = source_lines[source_line - 1]
            if (
                source_occurrences.occurrence_count(content) == 1
                and target_occurrences.occurrence_count(content) == 1
            ):
                anchor_source = source_line
                anchor_target = target_line

        if not anchor_source or anchor_target >= target_boundary:
            continue

        source_cursor = anchor_source + 1
        target_cursor = anchor_target + 1
        while source_cursor <= range_end and target_cursor <= target_boundary:
            if source_lines[source_cursor - 1] != target_lines[target_cursor - 1]:
                source_cursor += 1
                continue
            source_target = ordinary_mapping.get_target_line_from_source_line(
                source_cursor
            )
            target_source = ordinary_mapping.get_source_line_from_target_line(
                target_cursor
            )
            if source_target == target_cursor or (
                source_target is None
                and (
                    target_source is None
                    or target_source not in controlled_source_lines
                )
            ):
                source_cursor += 1
                target_cursor += 1
                continue
            source_cursor += 1

        if target_cursor <= target_boundary:
            continue

        source_cursor = anchor_source + 1
        target_cursor = anchor_target + 1
        while target_cursor <= target_boundary:
            if source_lines[source_cursor - 1] != target_lines[target_cursor - 1]:
                source_cursor += 1
                continue
            source_target = ordinary_mapping.get_target_line_from_source_line(
                source_cursor
            )
            target_source = ordinary_mapping.get_source_line_from_target_line(
                target_cursor
            )
            if source_target == target_cursor:
                source_cursor += 1
                target_cursor += 1
                continue
            if source_target is None and (
                target_source is None or target_source not in controlled_source_lines
            ):
                corrections.append((source_cursor, target_cursor))
                authorized_targets[target_cursor - 1] = 1
                source_cursor += 1
                target_cursor += 1
                continue
            source_cursor += 1


def _deduplicate_sorted_corrections(corrections: MappedRecordVector) -> None:
    """Remove duplicate corrections."""
    write_index = 0
    previous: tuple[int, ...] | None = None
    for correction in corrections:
        if correction == previous:
            continue
        corrections[write_index] = correction
        write_index += 1
        previous = correction
    corrections.truncate(write_index)


def _corrections_have_conflicting_assignments(
    workspace: MatcherWorkspace,
    corrections: Sequence[tuple[int, ...]],
    target_line_count: int,
) -> bool:
    """Return whether corrected lines collide or appear out of order."""
    target_sources = workspace.int_vector(
        target_line_count,
        width=8,
        fill=0,
    )
    try:
        previous_source = 0
        previous_target = 0
        for source_line, target_line in corrections:
            if previous_source == source_line and previous_target != target_line:
                return True
            target_source = target_sources[target_line - 1]
            if target_source not in (0, source_line):
                return True
            target_sources[target_line - 1] = source_line
            previous_source = source_line
            previous_target = target_line
    finally:
        workspace.close_resource(target_sources)
    return False


def match_lines_preserving_unowned_context(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    *,
    ownership: BatchOwnership | None = None,
    presence_lines: LineRanges | None = None,
    preferred_context_lines: LineRanges | None = None,
    ordinary_mapping: LineMapping | None = None,
    anchor_pairs: Sequence[tuple[int, int]] = (),
    anchor_authorized_source_lines: LineRanges | None = None,
    replacement_alternatives: Sequence["ResolvedReplacementAlternative"] = (),
    spool_dir: str | Path | None = None,
    matcher: Callable[..., LineMapping] = match_lines,
) -> PresenceMappingResult:
    """Map unselected text before placing selected lines.

    Keep the usual line map unless a selected line took text that also appears
    outside the selection. Unique surrounding text or an exact saved location
    may choose the right copy. Otherwise report ambiguity. Large arrays are
    kept in temporary mapped files instead of Python objects for every line.
    """
    owned_ordinary: LineMapping | None = None
    context_mapping: LineMapping | None = None
    result: LineMapping | None = None
    transferred: PresenceMappingResult | None = None
    returning_result = False
    workspace_exit = _WorkspaceExitState()
    try:
        if preferred_context_lines is None:
            preferred_context_lines = LineRanges.empty()
        if anchor_authorized_source_lines is None:
            anchor_authorized_source_lines = LineRanges.empty()
        if ordinary_mapping is None:
            owned_ordinary = matcher(
                source_lines,
                target_lines,
                anchor_pairs=anchor_pairs,
                spool_dir=spool_dir,
            )
            ordinary_mapping = owned_ordinary
        if len(ordinary_mapping.source_to_target) != len(source_lines) or len(
            ordinary_mapping.target_to_source
        ) != len(target_lines):
            raise ValueError("ordinary line mapping has incompatible dimensions")

        with _tracked_matcher_workspace(
            workspace_exit,
            spool_dir=spool_dir,
        ) as workspace:
            collisions = workspace.record_vector(
                len(controlled_source_lines),
                "QQ",
            )
            unowned_occurrences = LinePayloadOccurrenceIndex(
                workspace,
                source_lines,
                normalize_payloads=False,
                target_indexes=(
                    source_index
                    for source_index in range(len(source_lines))
                    if source_index + 1 not in controlled_source_lines
                ),
            )
            has_controlled_duplicates = False
            for range_start, range_end in controlled_source_lines.ranges():
                for source_line in range(
                    max(1, range_start),
                    min(len(source_lines), range_end) + 1,
                ):
                    if (
                        unowned_occurrences.occurrence_count(
                            source_lines[source_line - 1]
                        )
                        == 0
                    ):
                        continue
                    has_controlled_duplicates = True
                    target_line = ordinary_mapping.get_target_line_from_source_line(
                        source_line
                    )
                    if target_line is not None:
                        collisions.append((source_line, target_line))
            unowned_occurrences.close()

            has_unmapped_preferred_context = any(
                not ordinary_mapping.is_source_line_present(source_line)
                for range_start, range_end in preferred_context_lines.ranges()
                for source_line in range(range_start, range_end + 1)
            )
            if not has_controlled_duplicates and not has_unmapped_preferred_context:
                transferred = PresenceMappingResult(
                    ordinary_mapping,
                    owned_ordinary is not None,
                    PresenceMappingCorrection.ORDINARY,
                    PresenceMappingAmbiguity.NONE,
                )
                owned_ordinary = None
                returning_result = True
                return transferred

            source_occurrences = LinePayloadOccurrenceIndex(
                workspace,
                source_lines,
                normalize_payloads=False,
            )
            target_occurrences = LinePayloadOccurrenceIndex(
                workspace,
                target_lines,
                normalize_payloads=False,
            )
            ordinary_authorized_targets = workspace.int_vector(
                len(target_lines),
                width=4,
                fill=0,
            )
            context_authorized_targets = workspace.int_vector(
                len(target_lines),
                width=4,
                fill=0,
            )
            corrections = workspace.record_vector(
                len(controlled_source_lines) * 2,
                "QQ",
            )
            _mark_explicitly_anchored_controlled_lines(
                controlled_source_lines,
                anchor_authorized_source_lines,
                ordinary_mapping,
                anchor_pairs,
                ordinary_authorized_targets,
            )
            _mark_distinctively_anchored_controlled_spans(
                source_lines,
                target_lines,
                controlled_source_lines,
                ordinary_mapping,
                source_occurrences,
                target_occurrences,
                ordinary_authorized_targets,
            )
            presence_references = (
                None
                if ownership is None
                else EffectivePresenceReferenceIndex(workspace, ownership)
            )
            boundary_evidence = _presence_boundary_evidence(
                workspace,
                source_lines,
                target_lines,
                presence_references,
                (LineRanges.empty() if presence_lines is None else presence_lines),
            )
            _append_distinctive_controlled_run_extensions(
                source_lines,
                target_lines,
                controlled_source_lines,
                ordinary_mapping,
                source_occurrences,
                target_occurrences,
                corrections,
                ordinary_authorized_targets,
            )
            _append_local_context_extensions(
                source_lines,
                target_lines,
                controlled_source_lines,
                ordinary_mapping,
                source_occurrences,
                target_occurrences,
                corrections,
            )
            _append_coordinate_bounded_controlled_run_extensions(
                source_lines,
                target_lines,
                controlled_source_lines,
                ordinary_mapping,
                source_occurrences,
                target_occurrences,
                boundary_evidence.coordinate_boundaries,
                corrections,
                ordinary_authorized_targets,
            )
            if collisions or has_unmapped_preferred_context:
                context_mapping = match_uncontrolled_context_lines(
                    source_lines,
                    target_lines,
                    controlled_source_lines,
                    spool_dir=spool_dir,
                    matcher=matcher,
                )
                replacement_alternative_live_runs = _replacement_alternative_live_runs(
                    workspace,
                    replacement_alternatives,
                )
                _append_context_for_incomplete_controlled_runs(
                    source_lines,
                    target_lines,
                    controlled_source_lines,
                    ordinary_mapping,
                    context_mapping,
                    source_occurrences,
                    target_occurrences,
                    presence_references,
                    corrections,
                    context_authorized_targets,
                )
                _authorized_context_corrections(
                    source_lines,
                    target_lines,
                    controlled_source_lines,
                    preferred_context_lines,
                    boundary_evidence,
                    ordinary_mapping,
                    context_mapping,
                    replacement_alternatives,
                    replacement_alternative_live_runs,
                    source_occurrences,
                    target_occurrences,
                    corrections,
                    ordinary_authorized_targets,
                    context_authorized_targets,
                )
            sort_mapped_records(corrections)
            _deduplicate_sorted_corrections(corrections)
            has_conflicting_corrections = _corrections_have_conflicting_assignments(
                workspace,
                corrections,
                len(target_lines),
            )
            source_occurrences.close()
            target_occurrences.close()
            if has_conflicting_corrections:
                transferred = PresenceMappingResult(
                    ordinary_mapping,
                    owned_ordinary is not None,
                    PresenceMappingCorrection.ORDINARY,
                    PresenceMappingAmbiguity.COMPETING_CONTEXT,
                )
                owned_ordinary = None
                returning_result = True
                return transferred
            corrected_targets = workspace.int_vector(
                len(target_lines),
                width=4,
                fill=0,
            )
            for _source_line, target_line in corrections:
                corrected_targets[target_line - 1] = 1
            has_unresolved_collision = any(
                not corrected_targets[target_line - 1]
                and (
                    not ordinary_authorized_targets[target_line - 1]
                    or context_authorized_targets[target_line - 1]
                )
                for _source_line, target_line in collisions
            )
            has_competing_context = context_mapping is not None and any(
                not corrected_targets[target_line - 1]
                and (
                    not ordinary_authorized_targets[target_line - 1]
                    or context_authorized_targets[target_line - 1]
                )
                and context_mapping.get_source_line_from_target_line(target_line)
                is not None
                for _source_line, target_line in collisions
            )
            if not corrections:
                transferred = PresenceMappingResult(
                    ordinary_mapping,
                    owned_ordinary is not None,
                    PresenceMappingCorrection.ORDINARY,
                    _presence_mapping_ambiguity(
                        ambiguous=has_unresolved_collision,
                        competing_context=has_competing_context,
                    ),
                )
                owned_ordinary = None
                returning_result = True
                return transferred
            result = allocate_line_mapping(
                len(source_lines),
                len(target_lines),
                spool_dir=spool_dir,
            )
            correction_pairs = iter(corrections)
            next_correction = next(correction_pairs, None)
            collision_pairs = iter(collisions)
            next_collision = next(collision_pairs, None)
            previous_target = 0

            for source_line in range(1, len(source_lines) + 1):
                contested_target: int | None = None
                if next_collision is not None and next_collision[0] == source_line:
                    contested_target = next_collision[1]
                    next_collision = next(collision_pairs, None)
                is_displaced = (
                    contested_target is not None
                    and corrected_targets[contested_target - 1] != 0
                )
                chosen_target: int | None = None
                if next_correction is not None and next_correction[0] == source_line:
                    chosen_target = next_correction[1]
                    next_correction = next(correction_pairs, None)
                else:
                    ordinary_target = ordinary_mapping.get_target_line_from_source_line(
                        source_line
                    )
                    next_correction_target = (
                        len(target_lines) + 1
                        if next_correction is None
                        else next_correction[1]
                    )
                    if (
                        not is_displaced
                        and ordinary_target is not None
                        and previous_target < ordinary_target < next_correction_target
                        and result.target_to_source[ordinary_target - 1] == 0
                    ):
                        chosen_target = ordinary_target

                if chosen_target is None:
                    continue
                result.source_to_target[source_line - 1] = chosen_target
                result.target_to_source[chosen_target - 1] = source_line
                previous_target = chosen_target

            workspace.close_resource(corrections)
            workspace.close_resource(collisions)
            workspace.close_resource(corrected_targets)
            workspace.close_resource(context_authorized_targets)
            workspace.close_resource(ordinary_authorized_targets)

        result.may_have_unmapped_equal_lines = (
            ordinary_mapping.may_have_unmapped_equal_lines
            or (
                context_mapping is not None
                and context_mapping.may_have_unmapped_equal_lines
            )
        )
        transferred = PresenceMappingResult(
            result,
            True,
            PresenceMappingCorrection.CORRECTED,
            _presence_mapping_ambiguity(
                ambiguous=has_unresolved_collision,
                competing_context=has_competing_context,
            ),
        )
        result = None
        returning_result = True
        return transferred
    finally:
        active_error = not returning_result or not workspace_exit.completed
        if active_error and transferred is not None and transferred.owned:
            close_resources_preserving_first(
                (transferred.mapping,),
                suppress_errors=True,
            )
        try:
            close_resources_preserving_first(
                (result, context_mapping, owned_ordinary),
                suppress_errors=active_error,
            )
        except BaseException:
            if transferred is not None and transferred.owned:
                close_resources_preserving_first(
                    (transferred.mapping,),
                    suppress_errors=True,
                )
            raise
