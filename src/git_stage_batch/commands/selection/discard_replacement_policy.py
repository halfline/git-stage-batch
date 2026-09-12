"""Resolve replacement behavior and construct the chosen file content."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from contextlib import ExitStack
from itertools import chain

from ...batch.replacement_alternatives import ReplacementAlternativeOwnership
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.line_matching.sequence_equality import line_sequences_equal
from ...core.buffer import LineBuffer, buffer_ends_with_lf
from ...core.edit_plan import ReplacementEditPlan
from ...core.models import LineLevelChange
from ...core.replacement import ReplacementPayload, replacement_line_bodies
from ...batch.source.buffers import load_saved_session_file_as_buffer
from ...data.session import snapshot_file_if_untracked
from ...staging.content_buffers import (
    build_target_working_tree_buffer_with_edit_plan,
    build_target_working_tree_buffer_with_replaced_lines,
    replacement_baseline_span_indices,
    replacement_working_tree_span_indices,
)
from . import replacement_selection
from .discard_replacement_models import (
    _ReplacementDecision,
    _ReplacementBufferMode,
    _SavedReplacementPrefix,
    _ReplacementDestinationState,
)
from .discard_replacement_selection import (
    _contiguous_selected_addition_count,
    _line_body,
    _matching_baseline_prefix_context_count,
    _matching_discard_prefix_context_count,
    _refine_restoration_after_hidden_prefix,
    _replacement_payload_matches_line_span,
    _replacement_payload_retains_selected_addition,
    _requires_explicit_added_side_alternative,
    _selected_additions_cover_working_span,
    _selects_added_side_prefix,
    _selects_complete_old_partial_new_prefix,
)


@dataclass(frozen=True, slots=True)
class _ReplacementCandidate:
    """Selection facts for one attempt at an explicit replacement span."""

    effective_ids: set[int]
    baseline_start: int
    baseline_end: int
    worktree_start: int
    worktree_end: int
    uses_explicit_addition_span: bool
    has_explicit_addition_subspan: bool
    requested_run_has_deletion: bool
    restores_after_hidden_prefix: bool
    additions_cover_worktree_span: bool

    @property
    def line_count(self) -> int:
        return self.worktree_end - self.worktree_start

    @property
    def is_tracked_span(self) -> bool:
        return self.baseline_start < self.baseline_end and self.line_count > 0


def _replacement_candidate(
    line_changes: LineLevelChange,
    effective_ids: set[int],
    replacement_payload: ReplacementPayload,
    working_lines: LineBuffer,
    baseline_lines: LineBuffer,
    *,
    uses_explicit_addition_span: bool,
    has_explicit_addition_subspan: bool,
    requested_run_has_deletion: bool,
) -> _ReplacementCandidate:
    """Resolve source spans and exclude any hidden prefix restoration rows."""
    replacement_start, replacement_end = replacement_working_tree_span_indices(
        line_changes,
        effective_ids,
        len(working_lines),
        allow_incomplete_addition_span=uses_explicit_addition_span,
    )
    baseline_start, baseline_end = replacement_baseline_span_indices(
        line_changes,
        effective_ids,
        len(working_lines),
        allow_incomplete_addition_span=uses_explicit_addition_span,
    )
    restores_after_hidden_prefix = False
    if requested_run_has_deletion and not uses_explicit_addition_span:
        with replacement_line_bodies(replacement_payload) as payload_lines:
            refinement = _refine_restoration_after_hidden_prefix(
                line_changes,
                effective_ids,
                payload_lines,
                baseline_lines,
                working_lines,
                baseline_start=baseline_start,
                baseline_end=baseline_end,
                worktree_start=replacement_start,
                worktree_end=replacement_end,
            )
        if refinement is not None:
            replacement_start = refinement.worktree_span.start.offset
            effective_ids.difference_update(refinement.excluded_display_ids)
            restores_after_hidden_prefix = True
    return _ReplacementCandidate(
        effective_ids=effective_ids,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        worktree_start=replacement_start,
        worktree_end=replacement_end,
        uses_explicit_addition_span=uses_explicit_addition_span,
        has_explicit_addition_subspan=has_explicit_addition_subspan,
        requested_run_has_deletion=requested_run_has_deletion,
        restores_after_hidden_prefix=restores_after_hidden_prefix,
        additions_cover_worktree_span=_selected_additions_cover_working_span(
            line_changes,
            effective_ids,
            replacement_start=replacement_start,
            replacement_end=replacement_end,
        ),
    )


def _retained_payload_prefix(
    candidate: _ReplacementCandidate,
    payload_lines: Sequence[bytes],
    working_lines: LineBuffer,
    baseline_lines: LineBuffer,
    *,
    no_edge_overlap: bool,
) -> _SavedReplacementPrefix:
    """Keep a matching payload prefix and its verified adjacent context."""
    context_count = 0
    parent_context_count = 0
    if not no_edge_overlap:
        context_count = _matching_discard_prefix_context_count(
            payload_lines,
            working_lines,
            prefix_count=candidate.line_count,
            working_suffix_start=candidate.worktree_end,
            allow_content=candidate.is_tracked_span,
        )
        parent_context_count = _matching_baseline_prefix_context_count(
            baseline_lines,
            working_lines,
            baseline_suffix_start=candidate.baseline_end,
            working_suffix_start=candidate.worktree_end,
            maximum_count=context_count,
        )
    return _SavedReplacementPrefix(
        line_count=candidate.line_count + context_count,
        discard_context_count=context_count,
        parent_context_count=parent_context_count,
    )


def _added_side_ownership_scope(
    candidate: _ReplacementCandidate,
    payload_lines: Sequence[bytes],
    working_lines: LineBuffer,
    *,
    baseline_file_exists: bool,
    source_has_independent_session_edits: bool,
) -> ReplacementAlternativeOwnership:
    """Choose how much source an added-side alternative must preserve."""
    if (
        not payload_lines
        or candidate.has_explicit_addition_subspan
        or (
            baseline_file_exists
            and candidate.worktree_end + len(payload_lines) <= len(working_lines)
            and _replacement_payload_matches_line_span(
                payload_lines,
                working_lines,
                start=candidate.worktree_end,
                end=candidate.worktree_end + len(payload_lines),
            )
        )
        or source_has_independent_session_edits
    ):
        return ReplacementAlternativeOwnership.EXACT_SAVED_SPAN
    if not baseline_file_exists:
        return ReplacementAlternativeOwnership.UNTRACKED_SOURCE
    return ReplacementAlternativeOwnership.SOURCE_WITHOUT_LIVE


def _choose_saved_prefix(
    line_changes: LineLevelChange,
    candidate: _ReplacementCandidate,
    payload_lines: Sequence[bytes],
    working_lines: LineBuffer,
    baseline_lines: LineBuffer,
    *,
    destination: _ReplacementDestinationState,
    baseline_file_exists: bool,
    no_edge_overlap: bool,
    session_matches_worktree: Callable[[], bool],
) -> _SavedReplacementPrefix | None:
    """Choose the first applicable saved-prefix rule in precedence order."""
    complete_file_expansion = (
        len(payload_lines) > candidate.line_count
        and not baseline_file_exists
        and candidate.worktree_start == 0
        and candidate.worktree_end == len(working_lines)
        and session_matches_worktree()
    )
    if candidate.restores_after_hidden_prefix:
        return _SavedReplacementPrefix(
            candidate.line_count, prepend_selected_lines=True
        )
    if complete_file_expansion:
        return _SavedReplacementPrefix(
            candidate.line_count,
            prepend_selected_lines=True,
            ownership_scope=ReplacementAlternativeOwnership.EXACT_SAVED_SPAN,
        )
    if len(payload_lines) > candidate.line_count and all(
        payload_lines[index]
        == _line_body(working_lines[candidate.worktree_start + index])
        for index in range(candidate.line_count)
    ):
        return _retained_payload_prefix(
            candidate,
            payload_lines,
            working_lines,
            baseline_lines,
            no_edge_overlap=no_edge_overlap,
        )
    if (
        destination.file_exists
        and candidate.uses_explicit_addition_span
        and candidate.is_tracked_span
        and candidate.additions_cover_worktree_span
        and payload_lines
        and not _replacement_payload_matches_line_span(
            payload_lines,
            working_lines,
            start=candidate.worktree_start,
            end=candidate.worktree_end,
        )
    ):
        return _SavedReplacementPrefix(
            candidate.line_count, prepend_selected_lines=True
        )
    if (
        candidate.is_tracked_span
        and not _replacement_payload_matches_line_span(
            payload_lines,
            working_lines,
            start=candidate.worktree_start,
            end=candidate.worktree_end,
        )
        and _replacement_payload_retains_selected_addition(
            line_changes,
            candidate.effective_ids,
            payload_lines,
            baseline_lines,
        )
    ):
        return _SavedReplacementPrefix(
            candidate.line_count, prepend_selected_lines=True
        )
    if (
        candidate.additions_cover_worktree_span
        and (
            not candidate.has_explicit_addition_subspan
            or _selects_added_side_prefix(line_changes, candidate.effective_ids)
        )
        and candidate.baseline_start == candidate.baseline_end
        and _requires_explicit_added_side_alternative(
            payload_lines,
            working_lines,
            working_start=candidate.worktree_start,
            working_end=candidate.worktree_end,
            baseline_file_exists=baseline_file_exists,
            has_deletion_peer=candidate.requested_run_has_deletion,
            destination_has_file=destination.file_exists,
            no_edge_overlap=no_edge_overlap,
        )
    ):
        independent_edits = (
            not destination.file_exists and not session_matches_worktree()
        )
        return _SavedReplacementPrefix(
            candidate.line_count,
            prepend_selected_lines=True,
            ownership_scope=_added_side_ownership_scope(
                candidate,
                payload_lines,
                working_lines,
                baseline_file_exists=baseline_file_exists,
                source_has_independent_session_edits=independent_edits,
            ),
        )
    return None


def _decide_replacement_candidate(
    line_changes: LineLevelChange,
    candidate: _ReplacementCandidate,
    replacement_payload: ReplacementPayload,
    working_lines: LineBuffer,
    baseline_lines: LineBuffer,
    *,
    destination: _ReplacementDestinationState,
    baseline_file_exists: bool,
    no_edge_overlap: bool,
    session_matches_worktree: Callable[[], bool],
) -> _ReplacementDecision | None:
    """Return a content strategy, or request expansion of the selected span."""
    saved_prefix = None
    retains_subspan = False
    if candidate.line_count > 0 and (
        _selects_complete_old_partial_new_prefix(line_changes, candidate.effective_ids)
        or candidate.additions_cover_worktree_span
        or candidate.is_tracked_span
    ):
        with replacement_line_bodies(replacement_payload) as payload_lines:
            retains_subspan = candidate.has_explicit_addition_subspan and bool(
                payload_lines
            )
            saved_prefix = _choose_saved_prefix(
                line_changes,
                candidate,
                payload_lines,
                working_lines,
                baseline_lines,
                destination=destination,
                baseline_file_exists=baseline_file_exists,
                no_edge_overlap=no_edge_overlap,
                session_matches_worktree=session_matches_worktree,
            )
    if (
        candidate.uses_explicit_addition_span
        and saved_prefix is None
        and not retains_subspan
    ):
        return None
    if saved_prefix is not None and saved_prefix.prepend_selected_lines:
        buffer_mode = _ReplacementBufferMode.SAVED_THEN_LIVE
    elif retains_subspan and saved_prefix is None:
        buffer_mode = _ReplacementBufferMode.EXPLICIT_SPAN
    else:
        buffer_mode = _ReplacementBufferMode.SELECTED_LINES
    trim_edge_anchors = not no_edge_overlap and (
        buffer_mode is not _ReplacementBufferMode.SELECTED_LINES
        or saved_prefix is None
        or saved_prefix.discard_context_count > 0
    )
    if (
        saved_prefix is not None
        and saved_prefix.ownership_scope
        is ReplacementAlternativeOwnership.TRANSLATED_SELECTION
        and candidate.baseline_start >= candidate.baseline_end
        and _contiguous_selected_addition_count(line_changes, candidate.effective_ids)
        == candidate.line_count
    ):
        saved_prefix = replace(
            saved_prefix,
            ownership_scope=ReplacementAlternativeOwnership.EXACT_SAVED_SPAN,
        )
    return _ReplacementDecision(
        effective_ids=candidate.effective_ids,
        baseline_start=candidate.baseline_start,
        baseline_end=candidate.baseline_end,
        replacement_start=candidate.worktree_start,
        replacement_end=candidate.worktree_end,
        saved_prefix=saved_prefix,
        buffer_mode=buffer_mode,
        trim_edge_anchors=trim_edge_anchors,
        preserve_selected_addition_wording=(
            candidate.baseline_start == candidate.baseline_end
            and saved_prefix is None
            and candidate.requested_run_has_deletion
        ),
    )


def _resolve_replacement_decision(
    line_changes: LineLevelChange,
    requested_ids: set[int],
    effective_ids: set[int],
    replacement_payload: ReplacementPayload,
    working_lines: LineBuffer,
    baseline_lines: LineBuffer,
    source_stack: ExitStack,
    *,
    destination: _ReplacementDestinationState,
    baseline_file_exists: bool,
    uses_explicit_addition_span: bool,
    has_explicit_addition_subspan: bool,
    requested_run_has_deletion: bool,
    no_edge_overlap: bool,
) -> _ReplacementDecision:
    """Try the explicit selection, then its expanded form when necessary."""

    def session_matches_worktree() -> bool:
        if not baseline_file_exists:
            snapshot_file_if_untracked(line_changes.path)
        session_start_lines = source_stack.enter_context(
            load_saved_session_file_as_buffer(line_changes.path)
        )
        return line_sequences_equal(session_start_lines, working_lines)

    def decide(
        selected_ids: set[int], *, explicit_span: bool
    ) -> _ReplacementDecision | None:
        candidate = _replacement_candidate(
            line_changes,
            selected_ids,
            replacement_payload,
            working_lines,
            baseline_lines,
            uses_explicit_addition_span=explicit_span,
            has_explicit_addition_subspan=has_explicit_addition_subspan,
            requested_run_has_deletion=requested_run_has_deletion,
        )
        return _decide_replacement_candidate(
            line_changes,
            candidate,
            replacement_payload,
            working_lines,
            baseline_lines,
            destination=destination,
            baseline_file_exists=baseline_file_exists,
            no_edge_overlap=no_edge_overlap,
            session_matches_worktree=session_matches_worktree,
        )

    decision = decide(effective_ids, explicit_span=uses_explicit_addition_span)
    if decision is not None:
        return decision
    expanded_ids = replacement_selection.expand_replacement_selection_ids(
        line_changes,
        requested_ids,
        preserve_partial_addition_prefix=True,
    )
    decision = decide(expanded_ids, explicit_span=False)
    assert decision is not None
    return decision


def _build_replacement_buffer(
    line_changes: LineLevelChange,
    decision: _ReplacementDecision,
    edit_plan: ReplacementEditPlan,
    replacement_payload: ReplacementPayload,
    working_lines: LineBuffer,
) -> LineBuffer:
    """Construct replacement bytes using the resolved ownership policy."""
    if decision.buffer_mode is _ReplacementBufferMode.SAVED_THEN_LIVE:
        with build_target_working_tree_buffer_with_edit_plan(
            edit_plan,
            replacement_payload,
            working_lines,
            working_has_trailing_newline=buffer_ends_with_lf(working_lines),
            trim_unchanged_edge_anchors=decision.trim_edge_anchors,
        ) as live_replacement_buffer:
            rewritten_working_buffer = LineBuffer.from_chunks(
                chain(
                    LineRangeView(
                        live_replacement_buffer,
                        0,
                        decision.replacement_start,
                    ),
                    LineRangeView(
                        working_lines,
                        decision.replacement_start,
                        decision.replacement_end,
                    ),
                    LineRangeView(
                        live_replacement_buffer,
                        decision.replacement_start,
                        len(live_replacement_buffer),
                    ),
                )
            )
    elif decision.buffer_mode is _ReplacementBufferMode.EXPLICIT_SPAN:
        rewritten_working_buffer = build_target_working_tree_buffer_with_edit_plan(
            edit_plan,
            replacement_payload,
            working_lines,
            working_has_trailing_newline=buffer_ends_with_lf(working_lines),
            trim_unchanged_edge_anchors=decision.trim_edge_anchors,
        )
    else:
        rewritten_working_buffer = build_target_working_tree_buffer_with_replaced_lines(
            line_changes,
            decision.effective_ids,
            replacement_payload,
            working_lines,
            working_has_trailing_newline=buffer_ends_with_lf(working_lines),
            trim_unchanged_edge_anchors=decision.trim_edge_anchors,
            preserved_replacement_prefix_count=(
                decision.saved_prefix.line_count
                if decision.saved_prefix is not None
                else 0
            ),
        )
    return rewritten_working_buffer
