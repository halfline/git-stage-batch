"""Presence-aware structural line mapping."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Hashable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
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


_CONTROLLED_SOURCE_LINE = object()
_RECORDED_PRESENCE_BOUNDARY_FORMAT = "QQQ"


@dataclass(frozen=True, slots=True)
class PresenceMappingResult:
    """One mapping plus its ownership and context-correction state."""

    mapping: LineMapping
    owned: bool
    corrected: bool
    ambiguous: bool
    competing_context: bool


@dataclass(frozen=True, slots=True)
class _PresenceBoundaryEvidence:
    """Storage-backed referenced runs and uniquely proven live boundaries."""

    referenced_runs: MappedRecordVector
    unique_boundaries: MappedRecordVector


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
    if references is None or not presence_lines:
        return _PresenceBoundaryEvidence(referenced_runs, boundaries)

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
            if not _source_run_matches_recorded_boundary(
                source_lines,
                source_start,
                source_end,
                reference,
            ):
                continue
            referenced_runs.append((source_start, source_end))
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
    return _PresenceBoundaryEvidence(referenced_runs, boundaries)


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


def _authorized_context_corrections(
    source_lines: Sequence[bytes],
    target_lines: Sequence[bytes],
    controlled_source_lines: LineRanges,
    preferred_context_lines: LineRanges,
    boundary_evidence: _PresenceBoundaryEvidence,
    ordinary_mapping: LineMapping,
    context_mapping: LineMapping,
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
            ordinary_source_has_recorded_boundary = (
                ordinary_source is not None
                and sorted_line_ranges_contain(
                    boundary_evidence.referenced_runs,
                    ordinary_source,
                )
            )
            explicitly_authorized = ordinary_source is not None and (
                _run_contains_complete_preferred_context_range(
                    preferred_context_ranges,
                    source_start,
                    source_end,
                    source_line,
                )
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
            if (
                ordinary_source is None
                or ordinary_source == source_line
                or ordinary_source not in controlled_source_lines
                or not (explicitly_authorized or distinctively_authorized)
            ):
                continue
            context_authorized_targets[target_line - 1] = 1
            if not ordinary_authorized_targets[target_line - 1]:
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
    spool_dir: str | Path | None = None,
    matcher: Callable[..., LineMapping] = match_lines,
) -> PresenceMappingResult:
    """Map context before presence so claimed lines cannot steal live content.

    The ordinary mapping remains authoritative except where one of its selected
    source lines took content that also exists outside the selection. A
    distinctive context-only run can reassign that target. Otherwise the result
    is marked ambiguous: exact-coordinate planning may still prove the claim,
    but structural replay must refuse rather than infer ownership from the
    duplicate. Ordinary mappings survive between proven corrections. All
    file-sized vectors use mapped storage.
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
                    target_line = ordinary_mapping.get_target_line_from_source_line(
                        source_line
                    )
                    if target_line is not None:
                        collisions.append((source_line, target_line))
            unowned_occurrences.close()

            if not collisions:
                transferred = PresenceMappingResult(
                    ordinary_mapping,
                    owned_ordinary is not None,
                    False,
                    False,
                    False,
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
            _mark_distinctively_anchored_controlled_spans(
                source_lines,
                target_lines,
                controlled_source_lines,
                ordinary_mapping,
                source_occurrences,
                target_occurrences,
                ordinary_authorized_targets,
            )
            if collisions:
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
                context_mapping = match_uncontrolled_context_lines(
                    source_lines,
                    target_lines,
                    controlled_source_lines,
                    spool_dir=spool_dir,
                    matcher=matcher,
                )
                _authorized_context_corrections(
                    source_lines,
                    target_lines,
                    controlled_source_lines,
                    preferred_context_lines,
                    boundary_evidence,
                    ordinary_mapping,
                    context_mapping,
                    source_occurrences,
                    target_occurrences,
                    corrections,
                    ordinary_authorized_targets,
                    context_authorized_targets,
                )
            sort_mapped_records(corrections)
            source_occurrences.close()
            target_occurrences.close()
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
                    False,
                    has_unresolved_collision,
                    has_competing_context,
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
            True,
            has_unresolved_collision,
            has_competing_context,
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
