"""Prepare replacement selections while owning their source and result buffers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from itertools import chain
import os
from typing import cast
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.state.query import read_batch_metadata
from ...batch.selection import (
    parse_command_line_selection,
    require_line_selection_in_view,
)
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer
from ...core.coordinates import (
    BaselineSpace,
    DiffNewSpace,
    DiffOldSpace,
    DisplayLineId,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    WorktreeSpace,
    content_snapshot,
)
from ...core.edit_plan import ReplacementEditPlan
from ...core.selection_geometry import (
    ResolvedSelection,
    diff_view_identity,
    resolve_selection,
)
from ...core.models import LineLevelChange
from ...core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
    replacement_line_bodies,
)
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...exceptions import exit_with_error
from ...git_paths import display_path
from ...i18n import _
from ...staging.content_buffers import build_target_working_tree_buffer_from_lines
from ...utils.git_repository import get_git_repository_root_path
from . import replacement_selection
from .discard_replacement_models import (
    DiscardLineReplacementSelection,
    _ReplacementDestinationState,
    _ReplacementDecision,
)
from .discard_replacement_policy import (
    _build_replacement_buffer,
    _resolve_replacement_decision,
)
from .discard_replacement_selection import (
    _exclude_next_change_after_retained_suffix,
    _selected_run_has_deletion,
    _selected_run_has_unselected_addition,
)
from .discard_replacement_projection import (
    _project_rewritten_selection,
)


def _replacement_destination_state(
    batch_name: str,
    file_path: str,
) -> _ReplacementDestinationState:
    """Check whether this file is already in the destination batch."""
    file_exists = False
    if batch_exists(batch_name):
        files = read_batch_metadata(batch_name).get("files", {})
        file_exists = file_path in files
    return _ReplacementDestinationState(batch_name, file_exists)


@dataclass(frozen=True, slots=True)
class _ReplacementRequest:
    """Validated display selection and replacement input for this command."""

    line_changes: LineLevelChange
    destination: _ReplacementDestinationState
    requested_ids: set[int]
    effective_ids: set[int]
    replacement_payload: ReplacementPayload
    uses_explicit_addition_span: bool
    has_explicit_addition_subspan: bool
    requested_run_has_deletion: bool
    requested_selection_has_deletion: bool
    working_file_path: Path


def _load_replacement_request(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool,
) -> _ReplacementRequest:
    """Validate command input before acquiring replacement source buffers."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        exit_with_error(_("No selected hunk. Run 'start' first."))
    destination = _replacement_destination_state(batch_name, line_changes.path)
    requested_ids = set(parse_command_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    replacement_selection.require_contiguous_display_selection(requested_ids)
    replacement_payload = coerce_replacement_payload(replacement_text)
    if replacement_payload.exact and no_edge_overlap:
        with replacement_line_bodies(replacement_payload) as payload_lines:
            requested_ids = _exclude_next_change_after_retained_suffix(
                line_changes,
                requested_ids,
                payload_lines,
            )
    effective_ids, uses_explicit_addition_span = (
        replacement_selection.expand_replacement_selection_ids_with_explicit_span_status(
            line_changes,
            requested_ids,
            preserve_partial_addition_prefix=True,
        )
    )
    has_explicit_addition_subspan = (
        uses_explicit_addition_span
        and _selected_run_has_unselected_addition(
            line_changes,
            effective_ids,
        )
    )

    if not any(line.id in effective_ids for line in line_changes.lines):
        exit_with_error(
            _("No matching lines found for selection: {ids}").format(
                ids=line_id_specification
            )
        )

    requested_run_has_deletion = _selected_run_has_deletion(
        line_changes,
        requested_ids,
    )
    requested_selection_has_deletion = any(
        line.kind == "-" and line.id in requested_ids for line in line_changes.lines
    )

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not os.path.lexists(working_file_path):
        exit_with_error(
            _("File not found in working tree: {file}").format(
                file=display_path(line_changes.path)
            )
        )

    return _ReplacementRequest(
        line_changes=line_changes,
        destination=destination,
        requested_ids=requested_ids,
        effective_ids=effective_ids,
        replacement_payload=replacement_payload,
        uses_explicit_addition_span=uses_explicit_addition_span,
        has_explicit_addition_subspan=has_explicit_addition_subspan,
        requested_run_has_deletion=requested_run_has_deletion,
        requested_selection_has_deletion=requested_selection_has_deletion,
        working_file_path=working_file_path,
    )


def _bind_original_replacement(
    line_changes: LineLevelChange,
    decision: _ReplacementDecision,
    baseline_lines: LineBuffer,
    working_lines: LineBuffer,
) -> tuple[ResolvedSelection, ReplacementEditPlan]:
    """Bind requested display IDs and edit spans to their original snapshots."""
    baseline_snapshot_for_plan = cast(
        FileSnapshot[BaselineSpace],
        content_snapshot(
            line_changes.path,
            baseline_lines,
            space=BaselineSpace,
        ),
    )
    worktree_snapshot_for_plan = cast(
        FileSnapshot[WorktreeSpace],
        content_snapshot(
            line_changes.path,
            working_lines,
            space=WorktreeSpace,
        ),
    )
    edit_plan = ReplacementEditPlan(
        path=line_changes.path,
        baseline_snapshot=baseline_snapshot_for_plan,
        worktree_snapshot=worktree_snapshot_for_plan,
        baseline_span=LineSpan(
            LineBoundary(decision.baseline_start),
            LineBoundary(decision.baseline_end),
        ),
        worktree_span=LineSpan(
            LineBoundary(decision.replacement_start),
            LineBoundary(decision.replacement_end),
        ),
    )
    baseline_snapshot = cast(
        FileSnapshot[DiffOldSpace],
        content_snapshot(
            line_changes.path,
            baseline_lines,
            space=DiffOldSpace,
        ),
    )
    working_snapshot = cast(
        FileSnapshot[DiffNewSpace],
        content_snapshot(
            line_changes.path,
            working_lines,
            space=DiffNewSpace,
        ),
    )
    original_view = diff_view_identity(
        line_changes,
        old_snapshot=baseline_snapshot,
        new_snapshot=working_snapshot,
    )
    original_selection = resolve_selection(
        line_changes,
        (DisplayLineId(line_id) for line_id in decision.effective_ids),
        view=original_view,
    )
    return original_selection, edit_plan


@contextmanager
def prepare_discard_line_replacement_selection(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool = False,
) -> Iterator[DiscardLineReplacementSelection]:
    """Prepare rewritten line selection state for discard-to-batch."""
    request = _load_replacement_request(
        batch_name,
        line_id_specification,
        replacement_text,
        no_edge_overlap=no_edge_overlap,
    )
    try:
        with ExitStack() as source_stack:
            working_lines = source_stack.enter_context(
                load_working_tree_file_as_buffer(request.line_changes.path)
            )
            baseline_buffer = read_git_object_buffer_or_none(
                f"HEAD:{request.line_changes.path}"
            )
            baseline_file_exists = baseline_buffer is not None
            baseline_lines = source_stack.enter_context(
                baseline_buffer
                if baseline_buffer is not None
                else LineBuffer.from_bytes(b"")
            )
            decision = _resolve_replacement_decision(
                request.line_changes,
                request.requested_ids,
                request.effective_ids,
                request.replacement_payload,
                working_lines,
                baseline_lines,
                source_stack,
                destination=request.destination,
                baseline_file_exists=baseline_file_exists,
                uses_explicit_addition_span=request.uses_explicit_addition_span,
                has_explicit_addition_subspan=request.has_explicit_addition_subspan,
                requested_run_has_deletion=request.requested_run_has_deletion,
                no_edge_overlap=no_edge_overlap,
            )
            original_selection, edit_plan = _bind_original_replacement(
                request.line_changes,
                decision,
                baseline_lines,
                working_lines,
            )
            rewritten_working_buffer = _build_replacement_buffer(
                request.line_changes,
                decision,
                edit_plan,
                request.replacement_payload,
                working_lines,
            )
    except ValueError as error:
        exit_with_error(str(error))

    with rewritten_working_buffer as rewritten_working_lines:
        yield _project_rewritten_selection(
            request.line_changes,
            decision,
            request.replacement_payload,
            rewritten_working_lines,
            working_file_path=request.working_file_path,
            destination=request.destination,
            requested_selection_has_deletion=request.requested_selection_has_deletion,
            original_selection=original_selection,
            edit_plan=edit_plan,
        )


def build_discard_line_replacement_target_buffer(
    selection: DiscardLineReplacementSelection,
) -> LineBuffer:
    """Return the worktree buffer after removing rewritten replacement lines."""
    alternatives = selection.replacement_alternatives
    if alternatives is not None and (
        alternatives.requires_exact_saved_presence or alternatives.live is not None
    ):
        prefix_start, prefix_end = alternatives.saved_range
        return LineBuffer.from_chunks(
            chain(
                LineRangeView(selection.rewritten_working_lines, 0, prefix_start - 1),
                LineRangeView(
                    selection.rewritten_working_lines,
                    prefix_end,
                    len(selection.rewritten_working_lines),
                ),
            )
        )
    return build_target_working_tree_buffer_from_lines(
        selection.rewritten_line_changes,
        selection.rewritten_worktree_discard_ids,
        selection.rewritten_working_lines,
    )
