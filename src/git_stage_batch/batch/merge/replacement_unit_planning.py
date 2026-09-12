"""Plan replacement groups with scoped verification and mapped output records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector
from ..line_matching.line_mapping import LineMapping
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import LinePayloadOccurrenceIndex
from ..ownership.absence_claims import AbsenceClaim
from ..ownership.replacement_units import ReplacementUnit
from .baseline_edit_plan import BaselineEditPlan
from .candidates import MergeResolution as _MergeResolution
from .presence_reference_index import EffectivePresenceReferenceIndex
from .baseline_replacement_ranges import (
    collect_replacement_source_ranges as _collect_replacement_source_ranges,
)
from .baseline_anchor_matching import (
    unique_live_removal_edit as _unique_live_removal_edit,
)
from .validation import (
    build_mapped_source_line_index as _build_mapped_source_line_index,
    classify_replacement_old_side as _classify_replacement_old_side,
    ReplacementOldSideState as _ReplacementOldSideState,
)
from .baseline_replacement_edits import (
    _record_mapped_replacement_lines,
    _mixed_mapped_replacement_bounds,
    _deletion_target_position,
    _plan_relocated_replacement_from_presence_reference,
    _mapped_source_alternative_edit,
    _replacement_edit_fits_mapped_source_neighbors,
)
from .replacement_edit_locations import (
    _replacement_edit_from_trusted_target,
    _plan_partial_replacement_from_origin_resolution,
    _replacement_baseline_edit,
)
from .replacement_group_planning import (
    _TrustedPartialReplacementContext,
    _trusted_partial_replacement_context,
    _plan_complete_unrealized_origin_group,
    _plan_partial_replacement_from_trusted_target,
)


@dataclass(frozen=True, slots=True)
class _ReplacementSources:
    """Borrow file content and existing coordinate maps for one planning call."""

    source_sequence: Sequence[bytes] | None
    source_line_count: int
    working_lines: Sequence[bytes]
    trusted_target_lines: Sequence[bytes] | None
    source_to_working_mapping: LineMapping | None
    source_to_trusted_target_mapping: LineMapping | None
    trusted_target_to_working_mapping: LineMapping | None
    mapped_source_lines: Sequence[tuple[int, ...]] | None


@dataclass(frozen=True, slots=True)
class _ReplacementPolicy:
    """Caller-provided resolution and coordinate-trust policy."""

    resolution: _MergeResolution | None
    max_resolution_choices: int
    trust_baseline_coordinates: bool
    allow_mixed_mapped_replacement_islands: bool
    spool_dir: str | Path | None


@dataclass(frozen=True, slots=True)
class _ReplacementPlacement:
    """An accepted removal span and whether its coordinates were reviewed."""

    start: int
    end: int
    coordinate_was_reviewed: bool = True


@dataclass(frozen=True, slots=True)
class _ReplacementRecorder:
    """Write accepted unit ownership into the caller's mapped records."""

    plan: BaselineEditPlan
    deletion_edit_bounds: MappedRecordVector
    replacement_source_ranges: MappedRecordVector
    mapped_replacement_target_lines: MappedRecordVector

    def record(
        self,
        claimed_ranges: MappedRecordVector,
        deletion_index: int,
        placement: _ReplacementPlacement,
    ) -> None:
        for source_start, source_end in claimed_ranges:
            self.replacement_source_ranges.append((source_start, source_end))
        self.deletion_edit_bounds[deletion_index] = (
            1,
            placement.start,
            placement.end,
            placement.coordinate_was_reviewed,
        )


@dataclass(slots=True)
class _ReplacementUnitPlanner:
    """Traverse groups while sharing only one live occurrence index."""

    workspace: MatcherWorkspace
    sources: _ReplacementSources
    policy: _ReplacementPolicy
    recorder: _ReplacementRecorder
    replacement_units: Sequence[ReplacementUnit]
    deletion_claims: Sequence[AbsenceClaim]
    selected_presence: LineRanges
    presence_references: EffectivePresenceReferenceIndex | None
    live_occurrence_index: LinePayloadOccurrenceIndex | None = None
    previous_mixed_target_end: int = 0

    def plan_all(self) -> bool:
        unit_index = 0
        while unit_index < len(self.replacement_units):
            group_end = unit_index + 1
            origin = self.replacement_units[unit_index].origin
            if origin is not None:
                while (
                    group_end < len(self.replacement_units)
                    and self.replacement_units[group_end].origin == origin
                ):
                    group_end += 1
            if not self.plan_group(unit_index, group_end):
                return False
            unit_index = group_end
        return True

    def plan_group(self, unit_index: int, group_end: int) -> bool:
        """Try a complete group, then verify its children within one context."""
        origin = self.replacement_units[unit_index].origin
        if (
            origin is None
            and self.live_occurrence_index is None
            and self.sources.source_to_working_mapping is not None
            and self.sources.trusted_target_lines is not None
            and self.sources.source_to_trusted_target_mapping is not None
            and self.sources.trusted_target_to_working_mapping is not None
        ):
            self.live_occurrence_index = LinePayloadOccurrenceIndex(
                self.workspace,
                self.sources.working_lines,
            )
        if (
            self.sources.source_sequence is not None
            and (group_end > unit_index + 1 or origin is None)
            and _plan_complete_unrealized_origin_group(
                self.workspace,
                self.recorder.plan,
                self.sources.source_sequence,
                self.sources.working_lines,
                self.replacement_units,
                unit_index,
                group_end,
                self.deletion_claims,
                self.selected_presence,
                self.recorder.deletion_edit_bounds,
                self.recorder.replacement_source_ranges,
                self.sources.source_to_working_mapping,
                self.sources.mapped_source_lines,
                self.sources.trusted_target_lines,
                self.sources.source_to_trusted_target_mapping,
                self.sources.trusted_target_to_working_mapping,
                self.live_occurrence_index,
                spool_dir=self.policy.spool_dir,
            )
        ):
            return True
        partial_context = (
            None
            if (
                self.sources.source_sequence is None
                or (len(self.replacement_units) == 1 and self.policy.resolution is None)
            )
            else _trusted_partial_replacement_context(
                self.workspace,
                origin,
                self.sources.source_sequence,
                self.sources.working_lines,
                self.sources.trusted_target_lines,
                self.sources.source_to_working_mapping,
                self.sources.source_to_trusted_target_mapping,
                self.sources.trusted_target_to_working_mapping,
            )
        )

        try:
            for current_index in range(unit_index, group_end):
                if not self.plan_unit(current_index, partial_context):
                    return False
            return True
        finally:
            if partial_context is not None:
                partial_context.close()

    def plan_unit(
        self, unit_index: int, partial_context: _TrustedPartialReplacementContext | None
    ) -> bool:
        """Validate and plan one unit while owning its temporary range storage."""
        unit = self.replacement_units[unit_index]
        claimed_ranges = _collect_replacement_source_ranges(
            self.workspace,
            unit.presence_lines,
        )
        if claimed_ranges is None:
            return False
        try:
            if (
                not claimed_ranges
                or claimed_ranges[-1][1] > self.sources.source_line_count
                or len(unit.deletion_indices) != 1
            ):
                return False

            deletion_index = unit.deletion_indices[0]
            if (
                type(deletion_index) is not int
                or deletion_index < 0
                or deletion_index >= len(self.deletion_claims)
            ):
                return False
            if self.recorder.deletion_edit_bounds[deletion_index][0]:
                return False

            claim = self.deletion_claims[deletion_index]
            replacement_is_mapped = _record_mapped_replacement_lines(
                claimed_ranges,
                self.sources.source_to_working_mapping,
                self.recorder.mapped_replacement_target_lines,
            )
            if replacement_is_mapped is None:
                placement = self.plan_mixed_unit(unit, claim, claimed_ranges)
            elif replacement_is_mapped:
                placement = self.plan_mapped_unit(claim, claimed_ranges)
            else:
                placement = self.plan_unmapped_unit(
                    unit_index, unit, claim, claimed_ranges, partial_context
                )
            if placement is None:
                return False
            self.recorder.record(claimed_ranges, deletion_index, placement)
            return True
        finally:
            self.workspace.close_resource(claimed_ranges)

    def plan_mixed_unit(
        self,
        unit: ReplacementUnit,
        claim: AbsenceClaim,
        claimed_ranges: MappedRecordVector,
    ) -> _ReplacementPlacement | None:
        """Place mixed realization using trusted content or an ordered island."""
        trusted_mixed_edit = _replacement_edit_from_trusted_target(
            claim,
            unit.origin,
            claimed_ranges,
            self.sources.source_line_count,
            self.sources.source_sequence,
            self.sources.working_lines,
            self.sources.trusted_target_lines,
            self.sources.source_to_working_mapping,
            self.sources.source_to_trusted_target_mapping,
            self.sources.trusted_target_to_working_mapping,
            allow_mapped_source_predecessor=(len(self.replacement_units) == 1),
        )
        if trusted_mixed_edit is not None:
            target_start, target_end = trusted_mixed_edit
        else:
            mixed_bounds = (
                None
                if (
                    not self.policy.allow_mixed_mapped_replacement_islands
                    or self.sources.source_to_working_mapping is None
                )
                else _mixed_mapped_replacement_bounds(
                    claimed_ranges,
                    self.sources.source_to_working_mapping,
                    minimum_target_start=self.previous_mixed_target_end,
                )
            )
            if mixed_bounds is None:
                return None
            target_start, target_end = mixed_bounds
            self.previous_mixed_target_end = target_end
        self.recorder.plan.add_source_ranges(
            target_start,
            target_end,
            ((source_start, source_end) for source_start, source_end in claimed_ranges),
        )
        return _ReplacementPlacement(target_start, target_end)

    def plan_mapped_unit(
        self, claim: AbsenceClaim, claimed_ranges: MappedRecordVector
    ) -> _ReplacementPlacement | None:
        """Remove a verified old side without inserting already mapped presence."""
        assert self.sources.source_to_working_mapping is not None
        old_side = _classify_replacement_old_side(
            claim,
            self.sources.working_lines,
            self.sources.source_to_working_mapping,
            claimed_ranges,
            spool_dir=self.policy.spool_dir,
            mapped_source_lines=self.sources.mapped_source_lines,
        )
        if old_side is None or old_side.state not in (
            _ReplacementOldSideState.FULL,
            _ReplacementOldSideState.FULLY_CLAIMED,
            _ReplacementOldSideState.ABSENT,
        ):
            return None

        target_position = old_side.target_position
        if old_side.state in (
            _ReplacementOldSideState.FULLY_CLAIMED,
            _ReplacementOldSideState.ABSENT,
        ):
            target_position = _deletion_target_position(
                claim,
                self.sources.source_to_working_mapping,
            )
        if target_position is None:
            return None

        target_end = target_position
        if old_side.state is _ReplacementOldSideState.FULL:
            target_end += len(claim.content_lines)
            self.recorder.plan.add_removal(target_position, target_end)
        return _ReplacementPlacement(target_position, target_end)

    def plan_unmapped_unit(
        self,
        unit_index: int,
        unit: ReplacementUnit,
        claim: AbsenceClaim,
        claimed_ranges: MappedRecordVector,
        partial_context: _TrustedPartialReplacementContext | None,
    ) -> _ReplacementPlacement | None:
        """Try explicit, reviewed, and trusted partial placements in order."""
        relocated_bounds = (
            _plan_relocated_replacement_from_presence_reference(
                self.recorder.plan,
                claim,
                unit,
                claimed_ranges,
                self.sources.working_lines,
                self.presence_references,
            )
            if self.policy.trust_baseline_coordinates
            else None
        )
        if relocated_bounds is not None:
            return _ReplacementPlacement(*relocated_bounds)

        partial_reviewed_bounds = (
            None
            if self.sources.source_sequence is None
            else _plan_partial_replacement_from_origin_resolution(
                self.recorder.plan,
                claim,
                unit_index,
                unit,
                claimed_ranges,
                self.sources.source_line_count,
                self.sources.source_to_working_mapping,
                self.sources.working_lines,
                self.policy.resolution,
                max_results=self.policy.max_resolution_choices,
            )
        )
        if partial_reviewed_bounds is not None:
            return _ReplacementPlacement(*partial_reviewed_bounds)

        partial_trusted_bounds = (
            None
            if (
                self.sources.source_sequence is None
                or (len(self.replacement_units) == 1 and self.policy.resolution is None)
            )
            else _plan_partial_replacement_from_trusted_target(
                self.workspace,
                self.recorder.plan,
                claim,
                unit.origin,
                claimed_ranges,
                self.sources.source_sequence,
                self.sources.working_lines,
                partial_context,
                self.sources.source_to_working_mapping,
                self.sources.source_to_trusted_target_mapping,
                self.sources.trusted_target_to_working_mapping,
            )
        )
        if partial_trusted_bounds is not None:
            return _ReplacementPlacement(*partial_trusted_bounds)

        return self.plan_fallback_unit(unit_index, unit, claim, claimed_ranges)

    def plan_fallback_unit(
        self,
        unit_index: int,
        unit: ReplacementUnit,
        claim: AbsenceClaim,
        claimed_ranges: MappedRecordVector,
    ) -> _ReplacementPlacement | None:
        """Resolve a baseline edit and verify its mapped-source neighbors."""
        mapped_alternative_edit = _mapped_source_alternative_edit(
            claim,
            claimed_ranges,
            self.sources.source_sequence,
            self.sources.source_to_working_mapping,
        )
        replacement_edit = (
            (mapped_alternative_edit, True)
            if mapped_alternative_edit is not None
            else _replacement_baseline_edit(
                claim,
                unit_index,
                unit,
                claimed_ranges,
                self.sources.source_line_count,
                self.sources.source_sequence,
                self.sources.working_lines,
                self.sources.trusted_target_lines,
                self.sources.source_to_working_mapping,
                self.sources.source_to_trusted_target_mapping,
                self.sources.trusted_target_to_working_mapping,
                self.policy.resolution,
                max_resolution_choices=self.policy.max_resolution_choices,
                allow_mapped_source_predecessor=(len(self.replacement_units) == 1),
            )
        )
        if (
            replacement_edit is None
            and unit.origin is None
            and not self.policy.trust_baseline_coordinates
        ):
            if self.live_occurrence_index is None:
                self.live_occurrence_index = LinePayloadOccurrenceIndex(
                    self.workspace,
                    self.sources.working_lines,
                )
            shifted_edit = _unique_live_removal_edit(
                claim,
                self.sources.working_lines,
                self.live_occurrence_index,
            )
            if shifted_edit is not None:
                if (
                    not self.policy.trust_baseline_coordinates
                    and not _replacement_edit_fits_mapped_source_neighbors(
                        shifted_edit,
                        claim,
                        claimed_ranges,
                        self.sources.source_sequence,
                        len(self.sources.working_lines),
                        self.sources.source_to_working_mapping,
                        self.sources.mapped_source_lines,
                    )
                ):
                    return None
                replacement_edit = shifted_edit, True
        if replacement_edit is None:
            return None

        removal_edit, coordinate_was_reviewed = replacement_edit
        start, end = removal_edit
        if (
            not coordinate_was_reviewed
            and not self.policy.trust_baseline_coordinates
            and not _replacement_edit_fits_mapped_source_neighbors(
                removal_edit,
                claim,
                claimed_ranges,
                self.sources.source_sequence,
                len(self.sources.working_lines),
                self.sources.source_to_working_mapping,
                self.sources.mapped_source_lines,
            )
        ):
            return None
        self.recorder.plan.add_source_ranges(
            start,
            end,
            ((source_start, source_end) for source_start, source_end in claimed_ranges),
        )
        return _ReplacementPlacement(start, end, coordinate_was_reviewed)


def plan_replacement_unit_edits(
    workspace: MatcherWorkspace,
    plan: BaselineEditPlan,
    source_lines: Sequence[bytes] | int,
    working_lines: Sequence[bytes],
    replacement_units: Sequence[ReplacementUnit],
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    replacement_source_ranges: MappedRecordVector,
    mapped_replacement_target_lines: MappedRecordVector,
    resolution: _MergeResolution | None,
    *,
    max_resolution_choices: int,
    source_to_working_mapping: LineMapping | None,
    spool_dir: str | Path | None,
    selected_presence: LineRanges | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    trust_baseline_coordinates: bool = False,
    allow_mixed_mapped_replacement_islands: bool = False,
    mapped_source_lines: Sequence[tuple[int, ...]] | None = None,
    presence_references: EffectivePresenceReferenceIndex | None = None,
) -> bool:
    """Plan coupled replacements using borrowed sources and scoped groups."""
    if isinstance(source_lines, int):
        source_line_count = source_lines
        source_sequence = None
    else:
        source_line_count = len(source_lines)
        source_sequence = source_lines
    if selected_presence is None:
        selected_presence = LineRanges.from_specs(
            source_line
            for unit in replacement_units
            for source_line in unit.presence_lines
        )
    if mapped_source_lines is None and source_to_working_mapping is not None:
        mapped_source_lines = _build_mapped_source_line_index(
            workspace,
            source_to_working_mapping,
        )
    sources = _ReplacementSources(
        source_sequence,
        source_line_count,
        working_lines,
        trusted_target_lines,
        source_to_working_mapping,
        source_to_trusted_target_mapping,
        trusted_target_to_working_mapping,
        mapped_source_lines,
    )
    policy = _ReplacementPolicy(
        resolution,
        max_resolution_choices,
        trust_baseline_coordinates,
        allow_mixed_mapped_replacement_islands,
        spool_dir,
    )
    recorder = _ReplacementRecorder(
        plan,
        deletion_edit_bounds,
        replacement_source_ranges,
        mapped_replacement_target_lines,
    )
    return _ReplacementUnitPlanner(
        workspace,
        sources,
        policy,
        recorder,
        replacement_units,
        deletion_claims,
        selected_presence,
        presence_references,
    ).plan_all()
