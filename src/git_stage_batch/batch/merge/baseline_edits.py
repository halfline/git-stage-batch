"""Coordinate-based edit planning and streaming for batch merge."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector, sort_mapped_records
from .baseline_anchor_matching import (
    live_coordinate_edits_are_safe as _live_coordinate_edits_are_safe,
)
from .baseline_edit_plan import (
    BaselineEditPlan as _BaselineEditPlan,
    BaselineEditStream as _BaselineEditStream,
)
from .baseline_presence_edits import (
    plan_presence_insertions as _plan_presence_insertions,
)
from .baseline_removal_edits import (
    all_deletions_are_already_absent as _all_deletions_are_already_absent,
    plan_independent_removal_edits as _plan_independent_removal_edits,
)
from .baseline_replacement_edits import (
    plan_replacement_unit_edits as _plan_replacement_unit_edits,
    replacement_source_ranges_fit_presence as _replacement_source_ranges_fit_presence,
)
from .baseline_replacement_ranges import (
    replacement_source_range_capacity as _replacement_source_range_capacity,
)
from .presence_reference_index import EffectivePresenceReferenceIndex
from ..line_matching.sequence_equality import (
    line_sequences_equal as _line_sequences_match,
)
from ..line_matching.match_workspace import MatcherWorkspace
from .candidates import MergeResolution as _MergeResolution
from .validation import (
    build_mapped_source_line_index as _build_mapped_source_line_index,
)

if TYPE_CHECKING:
    from ..line_matching.line_mapping import LineMapping
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim


_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


def _has_complete_baseline_references(
    presence_references: EffectivePresenceReferenceIndex,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    for claimed_line in presence_line_set:
        reference = presence_references.reference_for(claimed_line)
        if reference is None or not reference.has_after_line:
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not reference.has_after_line:
            return False
    return bool(presence_line_set or deletion_claims)


def _recorded_constraints_are_already_satisfied(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    presence_references: EffectivePresenceReferenceIndex,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
) -> bool:
    """Return whether exact source identity satisfies recorded ownership."""
    return (
        _line_sequences_match(source_lines, working_lines)
        and _has_complete_baseline_references(
            presence_references,
            presence_line_set,
            deletion_claims,
        )
        and _all_deletions_are_already_absent(
            deletion_claims,
            working_lines,
        )
    )


def recorded_constraints_are_already_satisfied(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
    *,
    spool_dir: str | Path | None = None,
) -> bool:
    """Acquire scratch storage and check exact-source ownership satisfaction."""
    if _selection_outside_bounds(presence_line_set, len(source_lines)):
        return False

    with MatcherWorkspace(spool_dir=spool_dir) as workspace:
        presence_references = EffectivePresenceReferenceIndex(
            workspace,
            ownership,
        )
        return _recorded_constraints_are_already_satisfied(
            source_lines,
            working_lines,
            presence_references,
            presence_line_set,
            deletion_claims,
        )


def try_apply_baseline_coordinate_edits(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
    *,
    allow_adjacent_unmapped_presence: bool = False,
    allow_mapped_independent_removals: bool = False,
    allow_mixed_mapped_replacement_islands: bool = False,
    prefer_source_mapping_for_presence: bool = False,
    resolution: _MergeResolution | None = None,
    max_resolution_choices: int = _DEFAULT_RESOLUTION_CHOICE_LIMIT,
    trust_baseline_coordinates: bool = False,
    source_to_working_mapping: LineMapping | None = None,
    trusted_target_lines: Sequence[bytes] | None = None,
    source_to_trusted_target_mapping: LineMapping | None = None,
    trusted_target_to_working_mapping: LineMapping | None = None,
    spool_dir: str | Path | None = None,
) -> Iterator[bytes] | None:
    """Return content edited at recorded baseline coordinates, if safe.

    Combine replacement, removal, and insertion plans at positions saved from
    the batch baseline. By default, keep required source lines that are already
    present and reject coordinates whose saved boundary identifies more than
    one live-target location. Setting ``trust_baseline_coordinates`` makes the
    recorded positions authoritative. It skips live-target uniqueness and
    already-present insertion checks while retaining boundary, removal-content,
    and plan-consistency checks.

    ``allow_adjacent_unmapped_presence`` permits a missing claimed source run
    to be inserted immediately after its mapped predecessor. Callers should
    enable it only with a mapping constrained by trusted deletion anchors.

    ``allow_mapped_independent_removals`` and
    ``allow_mixed_mapped_replacement_islands`` are realization-only recovery
    proofs for a trusted predecessor. They permit structurally relocated old
    sides and a partially mapped, fully selected replacement island,
    respectively; ordinary live-target callers leave both disabled.

    ``prefer_source_mapping_for_presence`` tries that mapping before recorded
    presence coordinates. This leaves those coordinates available to a later
    fallback when source/replacement context cannot place the claimed lines.

    Return ``None`` if any selected edit cannot be planned safely. A plan that
    changes content returns a lazy stream that owns its planning workspace until
    the stream is exhausted or closed.
    """
    if _selection_outside_bounds(presence_line_set, len(source_lines)):
        return None

    workspace = MatcherWorkspace(spool_dir=spool_dir)
    try:
        presence_references = EffectivePresenceReferenceIndex(
            workspace,
            ownership,
        )
        if _recorded_constraints_are_already_satisfied(
            source_lines,
            working_lines,
            presence_references,
            presence_line_set,
            deletion_claims,
        ):
            workspace.close()
            return iter(working_lines)

        deletion_edit_bounds = workspace.record_vector(
            len(deletion_claims),
            "QQQQ",
            length=len(deletion_claims),
        )
        plan = _build_baseline_edit_plan(
            workspace,
            source_lines,
            working_lines,
            ownership,
            presence_line_set,
            deletion_claims,
            deletion_edit_bounds,
            presence_references,
            allow_adjacent_unmapped_presence=allow_adjacent_unmapped_presence,
            allow_mapped_independent_removals=(allow_mapped_independent_removals),
            allow_mixed_mapped_replacement_islands=(
                allow_mixed_mapped_replacement_islands
            ),
            prefer_source_mapping_for_presence=(prefer_source_mapping_for_presence),
            resolution=resolution,
            max_resolution_choices=max_resolution_choices,
            trust_baseline_coordinates=trust_baseline_coordinates,
            source_to_working_mapping=source_to_working_mapping,
            trusted_target_lines=trusted_target_lines,
            source_to_trusted_target_mapping=(source_to_trusted_target_mapping),
            trusted_target_to_working_mapping=(trusted_target_to_working_mapping),
            spool_dir=spool_dir,
        )
        if plan is None:
            workspace.close()
            return None

        workspace.close_resource(deletion_edit_bounds)
        if not plan:
            workspace.close()
            return iter(working_lines)

        return _BaselineEditStream(
            plan,
            source_lines,
            working_lines,
            workspace,
        )
    except BaseException:
        workspace.close()
        raise


def _build_baseline_edit_plan(
    workspace: MatcherWorkspace,
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: Sequence[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    presence_references: EffectivePresenceReferenceIndex,
    *,
    allow_adjacent_unmapped_presence: bool,
    allow_mapped_independent_removals: bool,
    allow_mixed_mapped_replacement_islands: bool,
    prefer_source_mapping_for_presence: bool,
    resolution: _MergeResolution | None,
    max_resolution_choices: int,
    trust_baseline_coordinates: bool,
    source_to_working_mapping: LineMapping | None,
    trusted_target_lines: Sequence[bytes] | None,
    source_to_trusted_target_mapping: LineMapping | None,
    trusted_target_to_working_mapping: LineMapping | None,
    spool_dir: str | Path | None,
) -> _BaselineEditPlan | None:
    """Build and validate one storage-backed exact-coordinate edit plan."""
    replacement_units = getattr(ownership, "replacement_units", [])
    presence_lines = coerce_line_ranges(presence_line_set)
    replacement_source_range_capacity = sum(
        _replacement_source_range_capacity(unit.presence_lines)
        for unit in replacement_units
    )
    plan = _BaselineEditPlan(
        workspace,
        edit_capacity=(
            len(replacement_units) + len(deletion_claims) + len(presence_lines)
        ),
        source_range_capacity=(replacement_source_range_capacity + len(presence_lines)),
    )
    replacement_source_ranges = workspace.record_vector(
        replacement_source_range_capacity,
        "QQ",
    )
    mapped_replacement_target_lines = workspace.record_vector(
        len(presence_lines),
        "Q",
    )
    mapped_source_lines = (
        None
        if source_to_working_mapping is None
        else _build_mapped_source_line_index(
            workspace,
            source_to_working_mapping,
        )
    )
    if not _plan_replacement_unit_edits(
        workspace,
        plan,
        source_lines,
        working_lines,
        replacement_units,
        deletion_claims,
        deletion_edit_bounds,
        replacement_source_ranges,
        mapped_replacement_target_lines,
        resolution,
        max_resolution_choices=max_resolution_choices,
        selected_presence=presence_lines,
        source_to_working_mapping=source_to_working_mapping,
        trusted_target_lines=trusted_target_lines,
        source_to_trusted_target_mapping=source_to_trusted_target_mapping,
        trusted_target_to_working_mapping=(trusted_target_to_working_mapping),
        trust_baseline_coordinates=trust_baseline_coordinates,
        allow_mixed_mapped_replacement_islands=(allow_mixed_mapped_replacement_islands),
        mapped_source_lines=mapped_source_lines,
        spool_dir=spool_dir,
    ):
        return None
    if not _replacement_source_ranges_fit_presence(
        presence_lines,
        replacement_source_ranges,
    ):
        return None
    if not _plan_independent_removal_edits(
        workspace,
        plan,
        working_lines,
        deletion_claims,
        deletion_edit_bounds,
        selected_presence=presence_lines,
        source_to_working_mapping=source_to_working_mapping,
        mapped_source_lines=mapped_source_lines,
        trusted_target_lines=trusted_target_lines,
        trusted_target_to_working_mapping=(trusted_target_to_working_mapping),
        allow_mapped_fallback=(
            allow_mapped_independent_removals or not trust_baseline_coordinates
        ),
        spool_dir=spool_dir,
    ):
        return None
    if mapped_replacement_target_lines:
        sort_mapped_records(mapped_replacement_target_lines)
        if not plan.sort_target_spans_and_validate() or plan.removes_any_target_lines(
            mapped_replacement_target_lines
        ):
            return None
    workspace.close_resource(mapped_replacement_target_lines)

    presence_insertion_plan = _plan_presence_insertions(
        plan,
        workspace,
        source_lines,
        working_lines,
        presence_references,
        presence_lines,
        replacement_source_ranges,
        allow_adjacent_unmapped_presence=allow_adjacent_unmapped_presence,
        prefer_source_mapping=prefer_source_mapping_for_presence,
        trust_baseline_coordinates=trust_baseline_coordinates,
        source_to_working_mapping=source_to_working_mapping,
        spool_dir=spool_dir,
    )
    if presence_insertion_plan is None:
        return None
    positioned_insertion_lines, owned_presence_mapping = presence_insertion_plan

    try:
        workspace.close_resource(replacement_source_ranges)
        if not trust_baseline_coordinates and not _live_coordinate_edits_are_safe(
            ownership,
            presence_references,
            working_lines,
            deletion_claims,
            deletion_edit_bounds,
            positioned_insertion_lines,
            source_to_working_mapping=source_to_working_mapping,
            presence_source_to_working_mapping=(
                source_to_working_mapping or owned_presence_mapping
            ),
            mapped_source_lines=mapped_source_lines,
            spool_dir=spool_dir,
        ):
            return None
    finally:
        if owned_presence_mapping is not None:
            owned_presence_mapping.close()
        if mapped_source_lines is not None:
            workspace.close_resource(mapped_source_lines)

    workspace.close_resource(positioned_insertion_lines)
    if not plan.sort_and_validate():
        return None
    return plan
