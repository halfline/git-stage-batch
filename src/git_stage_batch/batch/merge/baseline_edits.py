"""Coordinate-based edit planning and streaming for batch merge."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.line_selection import LineSelection, coerce_line_ranges
from ...core.mapped_storage import MappedRecordVector
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
from ..line_matching.sequence_equality import (
    line_sequences_equal as _line_sequences_match,
)
from ..line_matching.match_workspace import MatcherWorkspace
from .candidates import MergeResolution as _MergeResolution

if TYPE_CHECKING:
    from ..ownership.model import BatchOwnership
    from ..ownership.absence_claims import AbsenceClaim


_DEFAULT_RESOLUTION_CHOICE_LIMIT = 51


def _selection_outside_bounds(lines: LineSelection, max_line: int) -> bool:
    for line in lines:
        if line < 1 or line > max_line:
            return True
    return False


def _has_complete_baseline_references(
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
) -> bool:
    for claimed_line in presence_line_set:
        reference = ownership.presence_baseline_reference(claimed_line)
        if reference is None or not reference.has_after_line:
            return False
    for claim in deletion_claims:
        reference = claim.baseline_reference
        if reference is None or not reference.has_after_line:
            return False
    return bool(presence_line_set or deletion_claims)


def try_apply_baseline_coordinate_edits(
    source_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    ownership: BatchOwnership,
    presence_line_set: LineSelection,
    deletion_claims: list[AbsenceClaim],
    *,
    resolution: _MergeResolution | None = None,
    max_resolution_choices: int = _DEFAULT_RESOLUTION_CHOICE_LIMIT,
    trust_baseline_coordinates: bool = False,
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

    Return ``None`` if any selected edit cannot be planned safely. A plan that
    changes content returns a lazy stream that owns its planning workspace until
    the stream is exhausted or closed.
    """
    if _selection_outside_bounds(presence_line_set, len(source_lines)):
        return None

    if _line_sequences_match(
        source_lines, working_lines
    ) and _has_complete_baseline_references(
        ownership,
        presence_line_set,
        deletion_claims,
    ) and _all_deletions_are_already_absent(
        deletion_claims,
        working_lines,
    ):
        return iter(working_lines)

    workspace = MatcherWorkspace(spool_dir=spool_dir)
    try:
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
            resolution=resolution,
            max_resolution_choices=max_resolution_choices,
            trust_baseline_coordinates=trust_baseline_coordinates,
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
    deletion_claims: list[AbsenceClaim],
    deletion_edit_bounds: MappedRecordVector,
    *,
    resolution: _MergeResolution | None,
    max_resolution_choices: int,
    trust_baseline_coordinates: bool,
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
            len(replacement_units)
            + len(deletion_claims)
            + len(presence_lines)
        ),
        source_range_capacity=(
            replacement_source_range_capacity + len(presence_lines)
        ),
    )
    replacement_source_ranges = workspace.record_vector(
        replacement_source_range_capacity,
        "QQ",
    )
    if not _plan_replacement_unit_edits(
        workspace,
        plan,
        len(source_lines),
        working_lines,
        replacement_units,
        deletion_claims,
        deletion_edit_bounds,
        replacement_source_ranges,
        resolution,
        max_resolution_choices=max_resolution_choices,
    ):
        return None
    if not _replacement_source_ranges_fit_presence(
        presence_lines,
        replacement_source_ranges,
    ):
        return None
    if not _plan_independent_removal_edits(
        plan,
        working_lines,
        deletion_claims,
        deletion_edit_bounds,
    ):
        return None

    positioned_insertion_lines = _plan_presence_insertions(
        plan,
        workspace,
        source_lines,
        working_lines,
        ownership,
        presence_lines,
        replacement_source_ranges,
        trust_baseline_coordinates=trust_baseline_coordinates,
        spool_dir=spool_dir,
    )
    if positioned_insertion_lines is None:
        return None

    workspace.close_resource(replacement_source_ranges)
    if (
        not trust_baseline_coordinates
        and not _live_coordinate_edits_are_safe(
            ownership,
            working_lines,
            deletion_claims,
            deletion_edit_bounds,
            positioned_insertion_lines,
            spool_dir=spool_dir,
        )
    ):
        return None

    workspace.close_resource(positioned_insertion_lines)
    if not plan.sort_and_validate():
        return None
    return plan
