"""Project selected runs and rollback geometry into a rewritten file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ...batch.source.annotation import annotate_with_batch_source_working_lines
from ...batch.replacement_alternatives import (
    ExplicitReplacementAlternatives,
    ExplicitReplacementParent,
)
from ...batch.ownership import insertion_references as _insertion_references
from ...batch.transformed_selection import (
    NoRollback,
    RollbackSelection,
    TransformedSelectionProjection,
)
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.coordinates import (
    DiffNewSpace,
    DisplayLineId,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    content_snapshot,
    snapshot_as_role,
)
from ...core.edit_plan import AppliedReplacementEdit, ReplacementEditPlan
from ...core.selection_geometry import (
    ResolvedSelection,
    diff_view_identity,
    resolve_selection,
)
from ...core.models import LineLevelChange
from ...core.replacement import ReplacementPayload, replacement_line_bodies
from ...exceptions import exit_with_error
from ...git_paths import display_path
from ...i18n import _
from .discard_replacement_models import (
    DiscardLineReplacementSelection,
    _ReplacementDecision,
    _ReplacementBufferMode,
    _ReplacementDestinationState,
    _RewrittenSelectionRun,
)
from .discard_replacement_selection import (
    _verified_explicit_alternative_end,
)
from .discard_replacement_rendering import (
    _build_rewritten_line_changes,
    _rewritten_replacement_new_range,
)


@dataclass(frozen=True, slots=True)
class _RewrittenReplacementExtent:
    """Owned extent and optional live alternative in rewritten coordinates."""

    owned_replacement_new_end: int
    explicit_alternative_end: int | None
    materialize_owned_replacement_span: bool
    owned_replacement_line_count: int | None


def _rewritten_replacement_extent(
    decision: _ReplacementDecision,
    replacement_payload: ReplacementPayload,
    rewritten_working_lines: LineBuffer,
    *,
    replacement_new_start: int,
    replacement_new_end: int,
    requested_selection_has_deletion: bool,
) -> _RewrittenReplacementExtent:
    """Find the saved prefix, live alternative, and rows to materialize."""
    saved_prefix = decision.saved_prefix
    owned_prefix_count = saved_prefix.line_count if saved_prefix is not None else None
    owned_replacement_new_end = replacement_new_end
    if owned_prefix_count is not None:
        owned_replacement_new_end = min(
            replacement_new_end,
            replacement_new_start + owned_prefix_count - 1,
        )
    explicit_alternative_end: int | None = None
    if (
        owned_prefix_count is not None
        and owned_replacement_new_end < replacement_new_end
    ):
        if decision.buffer_mode is _ReplacementBufferMode.SAVED_THEN_LIVE:
            explicit_alternative_end = replacement_new_end
        else:
            with replacement_line_bodies(replacement_payload) as payload_lines:
                explicit_alternative_end = _verified_explicit_alternative_end(
                    selection_lines=rewritten_working_lines,
                    payload_lines=payload_lines,
                    owned_prefix_count=owned_prefix_count,
                    alternative_start=owned_replacement_new_end + 1,
                    fallback_end=replacement_new_end,
                )
    materialize_owned_replacement_span = (
        decision.preserve_selected_addition_wording
        or (
            replacement_payload.exact
            and owned_prefix_count is None
            and requested_selection_has_deletion
        )
    )
    owned_replacement_line_count = owned_prefix_count
    if (
        replacement_payload.exact
        and owned_prefix_count is None
        and requested_selection_has_deletion
    ):
        owned_replacement_line_count = replacement_new_end - replacement_new_start + 1
    return _RewrittenReplacementExtent(
        owned_replacement_new_end=owned_replacement_new_end,
        explicit_alternative_end=explicit_alternative_end,
        materialize_owned_replacement_span=materialize_owned_replacement_span,
        owned_replacement_line_count=owned_replacement_line_count,
    )


def _reanchor_rewritten_selection(
    rewritten_line_changes: LineLevelChange,
    rewritten_selected_ids: LineRanges,
    rewritten_working_lines: LineBuffer,
) -> LineRanges:
    """Update addition anchors after relocated context and retain their IDs."""
    selected_rewritten_lines = [
        line
        for line in rewritten_line_changes.lines
        if line.id is not None and line.id in rewritten_selected_ids
    ]
    reanchored_selected_lines = _insertion_references.reanchor_selected_additions_after_relocated_context_boundary(
        rewritten_line_changes.lines,
        selected_rewritten_lines,
        rewritten_working_lines,
    )
    reanchored_by_id = {
        line.id: line for line in reanchored_selected_lines if line.id is not None
    }
    if reanchored_by_id:
        for line_index, line in enumerate(rewritten_line_changes.lines):
            if line.id in reanchored_by_id:
                rewritten_line_changes.lines[line_index] = reanchored_by_id[line.id]
        rewritten_selected_ids = rewritten_selected_ids.union(
            LineRanges.from_lines(reanchored_by_id)
        )
    return rewritten_selected_ids


def _build_replacement_alternatives(
    decision: _ReplacementDecision,
    extent: _RewrittenReplacementExtent,
    applied_edit: AppliedReplacementEdit,
    *,
    replacement_new_start: int,
) -> ExplicitReplacementAlternatives | None:
    """Bind the saved prefix and optional parent to the applied edit."""
    saved_prefix = decision.saved_prefix
    rewritten_snapshot = applied_edit.rewritten_snapshot
    replacement_alternatives = None
    replacement_parent = None
    if saved_prefix is not None and decision.baseline_start < decision.baseline_end:
        replacement_parent = ExplicitReplacementParent(
            baseline=SnapshotSpan(
                applied_edit.plan.baseline_snapshot,
                LineSpan(
                    LineBoundary(decision.baseline_start),
                    LineBoundary(
                        decision.baseline_end + saved_prefix.parent_context_count
                    ),
                ),
            ),
            worktree=SnapshotSpan(
                applied_edit.plan.worktree_snapshot,
                LineSpan(
                    LineBoundary(decision.replacement_start),
                    LineBoundary(
                        decision.replacement_end + saved_prefix.parent_context_count
                    ),
                ),
            ),
        )
    if saved_prefix is not None:
        saved_span = SnapshotSpan(
            rewritten_snapshot,
            LineSpan(
                LineBoundary(replacement_new_start - 1),
                LineBoundary(extent.owned_replacement_new_end),
            ),
        )
        live_span = (
            SnapshotSpan(
                rewritten_snapshot,
                LineSpan(
                    saved_span.span.end,
                    LineBoundary(extent.explicit_alternative_end),
                ),
            )
            if extent.explicit_alternative_end is not None
            else None
        )
        replacement_alternatives = ExplicitReplacementAlternatives(
            edit=applied_edit,
            saved=saved_span,
            live=live_span,
            parent=replacement_parent,
            ownership_scope=saved_prefix.ownership_scope,
        )
    return replacement_alternatives


def _project_rewritten_selection(
    line_changes: LineLevelChange,
    decision: _ReplacementDecision,
    replacement_payload: ReplacementPayload,
    rewritten_working_lines: LineBuffer,
    *,
    working_file_path: Path,
    destination: _ReplacementDestinationState,
    requested_selection_has_deletion: bool,
    original_selection: ResolvedSelection,
    edit_plan: ReplacementEditPlan,
) -> DiscardLineReplacementSelection:
    """Bind saved ownership and rollback selections to the rewritten file."""
    rewritten_snapshot = cast(
        FileSnapshot[RewrittenWorktreeSpace],
        content_snapshot(
            line_changes.path,
            rewritten_working_lines,
            space=RewrittenWorktreeSpace,
        ),
    )
    replacement_new_start, replacement_new_end = _rewritten_replacement_new_range(
        line_changes,
        decision.effective_ids,
        rewritten_working_lines,
        original_working_line_count=edit_plan.worktree_snapshot.line_count,
        replacement_start=decision.replacement_start,
        replacement_end=decision.replacement_end,
    )
    extent = _rewritten_replacement_extent(
        decision,
        replacement_payload,
        rewritten_working_lines,
        replacement_new_start=replacement_new_start,
        replacement_new_end=replacement_new_end,
        requested_selection_has_deletion=requested_selection_has_deletion,
    )
    rewritten_cached_lines = _build_rewritten_line_changes(
        line_changes.path,
        rewritten_working_lines,
        rewritten_snapshot=rewritten_snapshot,
        materialized_new_start=(
            replacement_new_start if extent.materialize_owned_replacement_span else None
        ),
        materialized_new_end=(
            replacement_new_end if extent.materialize_owned_replacement_span else None
        ),
    )
    if rewritten_cached_lines is None:
        exit_with_error(
            _("No changes in file '{file}'.").format(
                file=display_path(line_changes.path)
            )
        )
    rewritten_line_changes = annotate_with_batch_source_working_lines(
        line_changes.path,
        rewritten_cached_lines,
        rewritten_working_lines,
    )
    _insertion_references.record_baseline_references_for_additions(
        rewritten_line_changes,
    )
    rewritten_selection_runs = _map_rewritten_selection_runs(
        line_changes,
        decision.effective_ids,
        rewritten_line_changes,
        replacement_new_start=replacement_new_start,
        replacement_new_end=extent.owned_replacement_new_end,
    )
    mapped_rewritten_ids = _combined_rewritten_selection_ids(
        rewritten_selection_runs,
        additional_ids=LineRanges.empty(),
    )
    rewritten_span_ids = _select_rewritten_span_ids(
        line_changes,
        decision.effective_ids,
        rewritten_line_changes,
        mapped_rewritten_ids=mapped_rewritten_ids,
        baseline_start=decision.baseline_start,
        baseline_end=decision.baseline_end,
        replacement_new_start=replacement_new_start,
        replacement_new_end=extent.owned_replacement_new_end,
        addition_target_count=extent.owned_replacement_line_count,
    )
    rewritten_selected_ids = _combined_rewritten_selection_ids(
        rewritten_selection_runs,
        additional_ids=rewritten_span_ids,
    )
    rewritten_selected_ids = _reanchor_rewritten_selection(
        rewritten_line_changes,
        rewritten_selected_ids,
        rewritten_working_lines,
    )
    rewritten_worktree_discard_ids = _rewritten_worktree_discard_ids(
        rewritten_selection_runs,
        rewritten_span_ids,
        rewritten_line_changes,
        preserve_selected_additions=decision.preserve_selected_addition_wording,
    )
    rewritten_view = diff_view_identity(
        rewritten_line_changes,
        old_snapshot=original_selection.view.old_snapshot,
        new_snapshot=snapshot_as_role(
            rewritten_snapshot,
            DiffNewSpace,
        ),
    )
    ownership_selection = resolve_selection(
        rewritten_line_changes,
        (DisplayLineId(line_id) for line_id in rewritten_selected_ids),
        view=rewritten_view,
    )
    rollback = (
        RollbackSelection(
            resolve_selection(
                rewritten_line_changes,
                (DisplayLineId(line_id) for line_id in rewritten_worktree_discard_ids),
                view=rewritten_view,
            )
        )
        if rewritten_worktree_discard_ids
        else NoRollback()
    )
    applied_edit = edit_plan.bind_result(
        rewritten_snapshot,
        replacement_line_count=(replacement_new_end - replacement_new_start + 1),
    )
    replacement_alternatives = _build_replacement_alternatives(
        decision,
        extent,
        applied_edit,
        replacement_new_start=replacement_new_start,
    )
    return DiscardLineReplacementSelection(
        line_changes=line_changes,
        transformed_projection=TransformedSelectionProjection(
            original_selection=original_selection,
            explicit_edit=applied_edit,
            rewritten_snapshot=rewritten_snapshot,
            ownership_selection=ownership_selection,
            rollback=rollback,
        ),
        file_path=line_changes.path,
        working_file_path=working_file_path,
        rewritten_line_changes=rewritten_line_changes,
        rewritten_selection_runs=rewritten_selection_runs,
        rewritten_selected_ids=rewritten_selected_ids,
        rewritten_worktree_discard_ids=rewritten_worktree_discard_ids,
        rewritten_working_lines=rewritten_working_lines,
        destination=destination,
        replacement_alternatives=replacement_alternatives,
    )


def _advance_ordered_range_membership(
    ranges: tuple[tuple[int, int], ...],
    range_index: int,
    value: int,
) -> tuple[int, bool]:
    """Test an ordered value while advancing through normalized ranges."""
    while range_index < len(ranges) and ranges[range_index][1] < value:
        range_index += 1
    return (
        range_index,
        range_index < len(ranges) and ranges[range_index][0] <= value,
    )


def _select_rewritten_span_ids(
    original_line_changes: LineLevelChange,
    selected_ids: set[int],
    rewritten_line_changes: LineLevelChange,
    *,
    mapped_rewritten_ids: LineRanges,
    baseline_start: int,
    baseline_end: int,
    replacement_new_start: int,
    replacement_new_end: int,
    addition_target_count: int | None,
) -> LineRanges:
    """Select the rewritten delta inside the original replacement envelope."""
    original_old_builder = LineRangeBuilder()
    original_new_builder = LineRangeBuilder()
    original_addition_count = 0
    for line in original_line_changes.lines:
        if line.id not in selected_ids:
            continue
        if line.old_line_number is not None:
            original_old_builder.add_line(line.old_line_number)
        if line.new_line_number is not None:
            original_new_builder.add_line(line.new_line_number)
        if line.kind == "+":
            original_addition_count += 1
    original_old_lines = original_old_builder.finish()
    original_new_lines = original_new_builder.finish()
    if addition_target_count is not None:
        original_addition_count = max(
            original_addition_count,
            addition_target_count,
        )

    coordinate_envelope_start: int | None = None
    coordinate_envelope_end: int | None = None
    original_old_ranges = original_old_lines.ranges()
    original_new_ranges = original_new_lines.ranges()
    original_old_range_index = 0
    original_new_range_index = 0
    for index, line in enumerate(rewritten_line_changes.lines):
        old_coordinate_selected = False
        if line.old_line_number is not None:
            original_old_range_index, old_coordinate_selected = (
                _advance_ordered_range_membership(
                    original_old_ranges,
                    original_old_range_index,
                    line.old_line_number,
                )
            )
        new_coordinate_selected = False
        if line.new_line_number is not None:
            original_new_range_index, new_coordinate_selected = (
                _advance_ordered_range_membership(
                    original_new_ranges,
                    original_new_range_index,
                    line.new_line_number,
                )
            )
        if line.kind != " " and (old_coordinate_selected or new_coordinate_selected):
            if coordinate_envelope_start is None:
                coordinate_envelope_start = index
            coordinate_envelope_end = index

    coordinate_addition_builder = LineRangeBuilder()
    if coordinate_envelope_start is not None and coordinate_envelope_end is not None:
        for index in range(
            coordinate_envelope_start,
            coordinate_envelope_end + 1,
        ):
            line = rewritten_line_changes.lines[index]
            if (
                line.kind == "+"
                and line.id is not None
                and line.new_line_number is not None
                and replacement_new_start <= line.new_line_number <= replacement_new_end
            ):
                coordinate_addition_builder.add_line(line.id)
    coordinate_addition_ids = coordinate_addition_builder.finish()

    mapped_rewritten_ranges = mapped_rewritten_ids.ranges()
    coordinate_addition_ranges = coordinate_addition_ids.ranges()
    mapped_rewritten_range_index = 0
    coordinate_addition_range_index = 0
    accounted_addition_count = 0
    for line in rewritten_line_changes.lines:
        if line.kind != "+" or line.id is None:
            continue
        mapped_rewritten_range_index, addition_is_mapped = (
            _advance_ordered_range_membership(
                mapped_rewritten_ranges,
                mapped_rewritten_range_index,
                line.id,
            )
        )
        coordinate_addition_range_index, addition_is_in_envelope = (
            _advance_ordered_range_membership(
                coordinate_addition_ranges,
                coordinate_addition_range_index,
                line.id,
            )
        )
        if addition_is_mapped or addition_is_in_envelope:
            accounted_addition_count += 1
    remaining_additions = max(
        original_addition_count - accounted_addition_count,
        0,
    )

    selected_ids_builder = LineRangeBuilder()
    mapped_rewritten_range_index = 0
    coordinate_addition_range_index = 0
    owned_prefix_remaining = addition_target_count
    for line in rewritten_line_changes.lines:
        line_id = line.id
        if line_id is None:
            continue
        addition_is_mapped = False
        addition_is_in_envelope = False
        if line.kind == "+":
            mapped_rewritten_range_index, addition_is_mapped = (
                _advance_ordered_range_membership(
                    mapped_rewritten_ranges,
                    mapped_rewritten_range_index,
                    line_id,
                )
            )
            coordinate_addition_range_index, addition_is_in_envelope = (
                _advance_ordered_range_membership(
                    coordinate_addition_ranges,
                    coordinate_addition_range_index,
                    line_id,
                )
            )
        if (
            line.kind == "-"
            and line.old_line_number is not None
            and baseline_start < line.old_line_number <= baseline_end
        ):
            selected_ids_builder.add_line(line_id)
        elif (
            line.kind == "+"
            and line.new_line_number is not None
            and replacement_new_start <= line.new_line_number <= replacement_new_end
            and (
                (owned_prefix_remaining is not None and owned_prefix_remaining > 0)
                or addition_is_in_envelope
                or (not addition_is_mapped and remaining_additions > 0)
            )
        ):
            selected_ids_builder.add_line(line_id)
            if owned_prefix_remaining is not None:
                owned_prefix_remaining -= 1
            if not addition_is_in_envelope:
                remaining_additions -= 1

    rewritten_span_ids = selected_ids_builder.finish()
    if not rewritten_span_ids and not mapped_rewritten_ids:
        exit_with_error(
            _("Replacement selection could not be located after rewriting the file.")
        )
    return rewritten_span_ids


@dataclass
class _SelectionRunProjection:
    """Compact projection state for one selected original change run."""

    original_old_lines: LineRanges
    original_new_lines: LineRanges
    addition_anchor: int | None
    addition_limit: int | None
    rewritten_old_lines: LineRangeBuilder
    rewritten_deletion_ids: LineRangeBuilder
    rewritten_addition_ids: LineRangeBuilder
    rewritten_addition_count: int = 0
    rewritten_old_range_index: int = 0

    def addition_window(self) -> tuple[int, int] | None:
        if self.original_old_lines:
            first_range = self.original_old_lines.ranges()[0]
            last_range = self.original_old_lines.ranges()[-1]
            return first_range[0] - 1, last_range[1]
        if self.addition_anchor is not None:
            return self.addition_anchor, self.addition_anchor
        return None


def _map_rewritten_selection_runs(
    original_line_changes: LineLevelChange,
    selected_ids: set[int],
    rewritten_line_changes: LineLevelChange,
    *,
    replacement_new_start: int,
    replacement_new_end: int,
) -> tuple[_RewrittenSelectionRun, ...]:
    """Project each selected original change run into the rewritten diff."""
    projections: list[_SelectionRunProjection] = []
    original_old_builder = LineRangeBuilder()
    original_new_builder = LineRangeBuilder()
    selected_addition_anchor: int | None = None
    selected_addition_count = 0
    block_has_selection = False

    def finish_original_block() -> None:
        nonlocal original_old_builder
        nonlocal original_new_builder
        nonlocal selected_addition_anchor
        nonlocal selected_addition_count
        nonlocal block_has_selection
        if block_has_selection:
            projections.append(
                _SelectionRunProjection(
                    original_old_lines=original_old_builder.finish(),
                    original_new_lines=original_new_builder.finish(),
                    addition_anchor=selected_addition_anchor,
                    addition_limit=(
                        selected_addition_count if selected_addition_count > 0 else None
                    ),
                    rewritten_old_lines=LineRangeBuilder(),
                    rewritten_deletion_ids=LineRangeBuilder(),
                    rewritten_addition_ids=LineRangeBuilder(),
                )
            )
        original_old_builder = LineRangeBuilder()
        original_new_builder = LineRangeBuilder()
        selected_addition_anchor = None
        selected_addition_count = 0
        block_has_selection = False

    original_coordinate_delta = (
        original_line_changes.header.old_prefix_line_count()
        - original_line_changes.header.new_prefix_line_count()
    )
    for line in original_line_changes.lines:
        if line.kind in {"+", "-"}:
            if line.id is not None and line.id in selected_ids:
                block_has_selection = True
                if line.old_line_number is not None:
                    original_old_builder.add_line(line.old_line_number)
                if line.new_line_number is not None:
                    original_new_builder.add_line(line.new_line_number)
                if (
                    line.kind == "+"
                    and selected_addition_anchor is None
                    and line.new_line_number is not None
                ):
                    selected_addition_anchor = (
                        line.new_line_number - 1 + original_coordinate_delta
                    )
                if line.kind == "+":
                    selected_addition_count += 1
            if line.kind == "+":
                original_coordinate_delta -= 1
            else:
                original_coordinate_delta += 1
            continue
        finish_original_block()
    finish_original_block()

    deletion_projection_index = 0
    addition_projection_index = 0
    rewritten_coordinate_delta = (
        rewritten_line_changes.header.old_prefix_line_count()
        - rewritten_line_changes.header.new_prefix_line_count()
    )
    for line in rewritten_line_changes.lines:
        if line.kind == "-" and line.old_line_number is not None:
            while deletion_projection_index < len(projections):
                projection = projections[deletion_projection_index]
                ranges = projection.original_old_lines.ranges()
                if not ranges or ranges[-1][1] < line.old_line_number:
                    deletion_projection_index += 1
                    continue
                (
                    projection.rewritten_old_range_index,
                    deletion_is_projected,
                ) = _advance_ordered_range_membership(
                    ranges,
                    projection.rewritten_old_range_index,
                    line.old_line_number,
                )
                if deletion_is_projected:
                    if line.id is not None:
                        projection.rewritten_deletion_ids.add_line(line.id)
                    projection.rewritten_old_lines.add_line(line.old_line_number)
                break
            rewritten_coordinate_delta += 1
            continue

        if line.kind != "+" or line.new_line_number is None:
            continue
        old_anchor = line.new_line_number - 1 + rewritten_coordinate_delta
        rewritten_coordinate_delta -= 1
        if not (replacement_new_start <= line.new_line_number <= replacement_new_end):
            continue
        while addition_projection_index < len(projections):
            projection = projections[addition_projection_index]
            window = projection.addition_window()
            if window is None or window[1] < old_anchor:
                addition_projection_index += 1
                continue
            if window[0] <= old_anchor <= window[1] and (
                projection.addition_limit is None
                or projection.rewritten_addition_count < projection.addition_limit
            ):
                if line.id is not None:
                    projection.rewritten_addition_ids.add_line(line.id)
                projection.rewritten_addition_count += 1
            break

    return tuple(
        _RewrittenSelectionRun(
            original_old_lines=projection.original_old_lines,
            original_new_lines=projection.original_new_lines,
            rewritten_deletion_ids=projection.rewritten_deletion_ids.finish(),
            rewritten_addition_ids=projection.rewritten_addition_ids.finish(),
            restore_deletions=(
                projection.rewritten_old_lines.finish() == projection.original_old_lines
            ),
        )
        for projection in projections
    )


def _combined_rewritten_selection_ids(
    selection_runs: tuple[_RewrittenSelectionRun, ...],
    *,
    additional_ids: LineRanges,
) -> LineRanges:
    return LineRanges.from_ranges(
        range_pair
        for selected_ids in (
            additional_ids,
            *(
                selected_ids
                for selection_run in selection_runs
                for selected_ids in (
                    selection_run.rewritten_deletion_ids,
                    selection_run.rewritten_addition_ids,
                )
            ),
        )
        for range_pair in selected_ids.ranges()
    )


def _rewritten_worktree_discard_ids(
    selection_runs: tuple[_RewrittenSelectionRun, ...],
    rewritten_span_ids: LineRanges,
    rewritten_line_changes: LineLevelChange,
    *,
    preserve_selected_additions: bool = False,
) -> LineRanges:
    """Choose rows to undo while preserving text the worktree still needs.

    An added line normally disappears when undone. If its block also deletes
    lines, that addition is part of a replacement and must stay.
    """
    protected_old_lines = LineRanges.from_ranges(
        range_pair
        for selection_run in selection_runs
        if not selection_run.restore_deletions
        for range_pair in selection_run.original_old_lines.ranges()
    )
    all_selected_ids = _combined_rewritten_selection_ids(
        selection_runs,
        additional_ids=rewritten_span_ids,
    )
    all_selected_ranges = all_selected_ids.ranges()
    protected_old_ranges = protected_old_lines.ranges()
    selected_range_index = 0
    protected_old_range_index = 0
    discard_builder = LineRangeBuilder()
    for line in rewritten_line_changes.lines:
        line_id = line.id
        if line_id is None:
            continue
        selected_range_index, line_is_selected = _advance_ordered_range_membership(
            all_selected_ranges,
            selected_range_index,
            line_id,
        )
        if not line_is_selected:
            continue
        old_line_is_protected = False
        if line.kind == "-" and line.old_line_number is not None:
            protected_old_range_index, old_line_is_protected = (
                _advance_ordered_range_membership(
                    protected_old_ranges,
                    protected_old_range_index,
                    line.old_line_number,
                )
            )
        if (line.kind == "+" and not preserve_selected_additions) or (
            line.kind == "-" and not old_line_is_protected
        ):
            discard_builder.add_line(line_id)
    return discard_builder.finish()
