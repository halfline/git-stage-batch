"""Line-replacement support for discard commands."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from itertools import chain
import os
from pathlib import Path
from typing import cast

from ...batch.complete_source_replacement import (
    materialize_untracked_source_replacement,
    promote_untracked_presence_to_complete_source_replacement,
    refresh_complete_source_replacement,
)
from ...batch.source.annotation import (
    acquire_batch_source_mapping,
    annotate_with_batch_source_working_lines,
)
from ...batch.replacement_alternatives import (
    ExplicitReplacementAlternatives,
    ExplicitReplacementParent,
    ReplacementAlternativeOwnership,
)
from ...batch.state.lifecycle import create_batch
from ...batch.ownership.metadata_loading import acquire_ownership_for_metadata_dict
from ...batch.ownership.hunk_translation import (
    translate_hunk_selection_to_batch_ownership,
)
from ...batch.ownership.merging import merge_batch_ownership
from ...batch.ownership import insertion_references as _insertion_references
from ...batch.ownership.remapping import remap_batch_ownership_with_lineage
from ...batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
    stream_replacement_line_runs_from_lines,
)
from ...batch.ownership.absence_claims import AbsenceClaim
from ...batch.line_matching.match import match_lines
from ...batch.line_matching.match_workspace import MatcherWorkspace
from ...batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.line_matching.transforms import (
    BatchSourceExactTransform,
    SameContentSpanProjection,
)
from ...batch.line_matching.sequence_equality import (
    line_sequences_equal,
    line_slice_equals,
)
from ...batch.ownership.line_entries import (
    baseline_reference_for_file_line_range,
    replacement_unit_origin_for_line_run,
)
from ...batch.ownership.references import BaselineReference
from ...batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from ...batch.ownership.replacement_units import normalize_replacement_units
from ...batch.ownership.replacement_origins import (
    ReplacementOriginSourceProjection,
    SameStreamReplacementOrigin,
)
from ...batch.ownership.claims import (
    presence_claims_from_source_lines,
)
from ...batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
)
from ...batch.merge.presence_reference_index import (
    EffectivePresenceReferenceIndex,
)
from ...batch.state.query import read_batch_metadata
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...batch.selection import (
    parse_command_line_selection,
    require_line_selection_in_view,
)
from ...batch.source.advancement import (
    advance_source_lines_preserving_existing_presence,
)
from ...batch.source.line_coordinates import (
    ExactLineageSourceCoordinates,
    IdentitySourceCoordinates,
    SourceCoordinateTransform,
    translate_display_source_coordinates,
)
from ...batch.source.projection import SourceCoordinateProjection
from ...batch.file_state import BatchMetadataRevision, SourceBoundOwnership
from ...batch.transformed_selection import (
    NoRollback,
    RollbackSelection,
    TransformedSelectionProjection,
)
from ...batch.text_file_storage import add_source_bound_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer, buffer_ends_with_lf
from ...core.text_lines import normalize_line_sequence_endings
from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.coordinates import (
    BaselineSpace,
    DiffNewSpace,
    DiffOldSpace,
    BatchSourceSpace,
    DisplayLineId,
    FileSnapshot,
    LineBoundary,
    LineSpan,
    RewrittenWorktreeSpace,
    SnapshotSpan,
    WorktreeSpace,
    content_snapshot,
    snapshot_as_role,
)
from ...core.edit_plan import ReplacementEditPlan
from ...core.selection_geometry import (
    diff_view_identity,
    resolve_selection,
)
from ...core.models import LineEntry, LineLevelChange
from ...core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
    replacement_line_bodies,
)
from ...batch.ownership.model import BatchOwnership
from ...batch.source.cache import (
    get_session_source_hint,
    load_session_batch_sources,
    save_session_batch_sources,
)
from ...batch.source.buffers import load_saved_session_file_as_buffer
from ...batch.source.snapshots import create_batch_source_commit
from ...data.file_modes import detect_file_mode
from ...data.file_hunk_display import build_file_hunk_from_buffer
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import (
    read_git_object_buffer_or_none,
    read_git_object_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ...data.session import snapshot_file_if_untracked
from ...exceptions import exit_with_error
from ...git_paths import display_path
from ...i18n import _
from ...staging.content_buffers import (
    build_target_working_tree_buffer_from_lines,
    build_target_working_tree_buffer_with_edit_plan,
    build_target_working_tree_buffer_with_replaced_lines,
    replacement_baseline_span_indices,
    replacement_working_tree_span_indices,
)
from ...utils.git_repository import get_git_repository_root_path
from . import replacement_selection


@dataclass(frozen=True)
class DiscardLineReplacementSelection:
    """Prepared replacement selection for discard-to-batch."""

    line_changes: LineLevelChange
    transformed_projection: TransformedSelectionProjection
    file_path: str
    working_file_path: Path
    rewritten_line_changes: LineLevelChange
    rewritten_selection_runs: tuple[_RewrittenSelectionRun, ...]
    rewritten_selected_ids: LineRanges
    rewritten_worktree_discard_ids: LineRanges
    rewritten_working_lines: LineBuffer
    destination: _ReplacementDestinationState
    replacement_alternatives: ExplicitReplacementAlternatives | None = None

    def __post_init__(self) -> None:
        ownership_ids = (
            self.transformed_projection.ownership_selection.display_ids.to_line_ranges()
        )
        if ownership_ids != self.rewritten_selected_ids:
            raise ValueError("ownership IDs differ from transformed projection")
        rollback_ids = LineRanges.empty()
        if isinstance(self.transformed_projection.rollback, RollbackSelection):
            rollback_ids = (
                self.transformed_projection.rollback.selection.display_ids.to_line_ranges()
            )
        if rollback_ids != self.rewritten_worktree_discard_ids:
            raise ValueError("rollback IDs differ from transformed projection")
        if (
            self.replacement_alternatives is not None
            and self.replacement_alternatives.edit
            != self.transformed_projection.explicit_edit
        ):
            raise ValueError("replacement alternatives differ from explicit edit")

@dataclass(frozen=True)
class _RewrittenSelectionRun:
    """One original changed run projected into the rewritten diff."""

    original_old_lines: LineRanges
    original_new_lines: LineRanges
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges
    restore_deletions: bool


@dataclass(frozen=True, slots=True)
class _ReplacementDestinationState:
    """What the destination batch already contains."""

    batch_name: str
    file_exists: bool


@dataclass(frozen=True, slots=True)
class _ReplacementSpanRefinement:
    """A narrower worktree span and the displayed rows left outside it."""

    worktree_span: LineSpan[WorktreeSpace]
    excluded_display_ids: LineRanges


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


def _line_through_last_identifier(content: bytes) -> bytes:
    """Remove indentation and punctuation after the last name character."""
    body = _line_body(content).strip()
    for index in range(len(body) - 1, -1, -1):
        byte = body[index]
        if (
            byte == ord("_")
            or ord("0") <= byte <= ord("9")
            or ord("A") <= byte <= ord("Z")
            or ord("a") <= byte <= ord("z")
            or byte >= 0x80
        ):
            return body[: index + 1]
    return b""


def _refine_restoration_after_hidden_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    payload_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    baseline_start: int,
    baseline_end: int,
    worktree_start: int,
    worktree_end: int,
) -> _ReplacementSpanRefinement | None:
    """Leave earlier added lines alone when a prior peel separates them."""
    baseline_count = baseline_end - baseline_start
    if (
        baseline_count <= 0
        or len(payload_lines) != baseline_count
        or worktree_end - worktree_start <= baseline_count
        or any(
            payload_lines[offset]
            != _line_body(baseline_lines[baseline_start + offset])
            for offset in range(baseline_count)
        )
    ):
        return None

    first_baseline_line = _line_body(baseline_lines[baseline_start])
    first_line_key = _line_through_last_identifier(first_baseline_line)
    if not first_line_key:
        return None

    candidate: int | None = None
    for working_index in range(worktree_start, worktree_end):
        if _line_through_last_identifier(working_lines[working_index]) != first_line_key:
            continue
        if candidate is not None:
            return None
        candidate = working_index
    if (
        candidate is None
        or candidate == worktree_start
        or _line_body(working_lines[candidate]) == first_baseline_line
        or worktree_end - candidate <= baseline_count
    ):
        return None

    excluded_ids = LineRangeBuilder()
    excluded_count = 0
    candidate_is_selected_addition = False
    for line in line_changes.lines:
        new_line = line.new_line
        if new_line == candidate + 1 and line.old_line is None:
            candidate_is_selected_addition = line.id in selected_ids
        elif worktree_start < new_line <= candidate:
            if line.id not in selected_ids:
                return None
            excluded_ids.add_line(line.id)
            excluded_count += 1
    if (
        not candidate_is_selected_addition
        or excluded_count != candidate - worktree_start
    ):
        return None

    source_hint = get_session_source_hint(line_changes.path)
    if source_hint is None:
        return None
    with acquire_batch_source_mapping(
        line_changes.path,
        batch_source_commit=source_hint.commit,
        working_lines=working_lines,
    ) as mapping:
        if mapping is None:
            return None
        preceding_source_line = mapping.get_source_line_from_target_line(candidate)
        candidate_source_line = mapping.get_source_line_from_target_line(candidate + 1)
        if (
            preceding_source_line is None
            or candidate_source_line is None
            or candidate_source_line <= preceding_source_line + 1
        ):
            return None

    return _ReplacementSpanRefinement(
        worktree_span=LineSpan(
            LineBoundary(candidate),
            LineBoundary(worktree_end),
        ),
        excluded_display_ids=excluded_ids.finish(),
    )


@contextmanager
def prepare_discard_line_replacement_selection(
    batch_name: str,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool = False,
) -> Iterator[DiscardLineReplacementSelection]:
    """Prepare rewritten line selection state for discard-to-batch."""
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
    requested_run_has_deletion = _selected_run_has_deletion(
        line_changes,
        requested_ids,
    )

    if not any(line.id in effective_ids for line in line_changes.lines):
        exit_with_error(
            _("No matching lines found for selection: {ids}").format(
                ids=line_id_specification
            )
        )

    working_file_path = get_git_repository_root_path() / line_changes.path
    if not os.path.lexists(working_file_path):
        exit_with_error(
            _("File not found in working tree: {file}").format(
                file=display_path(line_changes.path)
            )
        )

    replacement_owned_prefix_count: int | None = None
    replacement_discard_prefix_context_count = 0
    retains_explicit_addition_subspan = False
    materializes_saved_then_live = False
    replacement_ownership_scope = ReplacementAlternativeOwnership.TRANSLATED_SELECTION
    source_has_independent_session_edits: bool | None = None
    try:
        with ExitStack() as source_stack:
            working_lines = source_stack.enter_context(
                load_working_tree_file_as_buffer(line_changes.path)
            )
            baseline_buffer = read_git_object_buffer_or_none(
                f"HEAD:{line_changes.path}"
            )
            baseline_file_exists = baseline_buffer is not None
            baseline_lines = source_stack.enter_context(
                baseline_buffer
                if baseline_buffer is not None
                else LineBuffer.from_bytes(b"")
            )
            original_working_line_count = len(working_lines)
            while True:
                replacement_owned_prefix_count = None
                replacement_discard_prefix_context_count = 0
                retains_explicit_addition_subspan = False
                materializes_saved_then_live = False
                replacement_ownership_scope = (
                    ReplacementAlternativeOwnership.TRANSLATED_SELECTION
                )
                selects_partial_new_prefix = (
                    _selects_complete_old_partial_new_prefix(
                        line_changes,
                        effective_ids,
                    )
                )
                selected_addition_count = _contiguous_selected_addition_count(
                    line_changes,
                    effective_ids,
                )
                selects_added_side_prefix = _selects_added_side_prefix(
                    line_changes,
                    effective_ids,
                )
                selected_run_has_deletion = requested_run_has_deletion
                replacement_start, replacement_end = (
                    replacement_working_tree_span_indices(
                        line_changes,
                        effective_ids,
                        original_working_line_count,
                        allow_incomplete_addition_span=(
                            uses_explicit_addition_span
                        ),
                    )
                )
                baseline_start, baseline_end = replacement_baseline_span_indices(
                    line_changes,
                    effective_ids,
                    original_working_line_count,
                    allow_incomplete_addition_span=uses_explicit_addition_span,
                )
                restores_after_hidden_prefix = False
                if requested_run_has_deletion and not uses_explicit_addition_span:
                    with replacement_line_bodies(
                        replacement_payload
                    ) as payload_lines:
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
                        effective_ids.difference_update(
                            refinement.excluded_display_ids
                        )
                        restores_after_hidden_prefix = True
                selected_working_line_count = replacement_end - replacement_start
                selects_exact_addition_span = (
                    selected_addition_count is not None
                    and selected_addition_count == selected_working_line_count
                )
                selected_additions_cover_working_span = (
                    _selected_additions_cover_working_span(
                        line_changes,
                        effective_ids,
                        replacement_start=replacement_start,
                        replacement_end=replacement_end,
                    )
                )
                selects_explicit_tracked_span = (
                    baseline_start < baseline_end and selected_working_line_count > 0
                )
                if (
                    selects_partial_new_prefix
                    or selected_additions_cover_working_span
                    or selects_explicit_tracked_span
                ) and replacement_start < replacement_end:
                    with replacement_line_bodies(replacement_payload) as payload_lines:
                        retains_explicit_addition_subspan = (
                            has_explicit_addition_subspan and bool(payload_lines)
                        )
                        if restores_after_hidden_prefix:
                            replacement_owned_prefix_count = selected_working_line_count
                            materializes_saved_then_live = True
                        elif (
                            not baseline_file_exists
                            and selected_additions_cover_working_span
                            and baseline_start == baseline_end
                            and payload_lines
                            and (
                                len(payload_lines) != selected_working_line_count
                                or any(
                                    payload_lines[index]
                                    != _line_body(
                                        working_lines[replacement_start + index]
                                    )
                                    for index in range(selected_working_line_count)
                                )
                            )
                        ):
                            snapshot_file_if_untracked(line_changes.path)
                            session_start_lines = source_stack.enter_context(
                                load_saved_session_file_as_buffer(line_changes.path)
                            )
                            if len(session_start_lines) == len(working_lines) and all(
                                _line_body(session_start_lines[index])
                                == _line_body(working_lines[index])
                                for index in range(len(working_lines))
                            ):
                                replacement_owned_prefix_count = (
                                    selected_working_line_count
                                )
                                materializes_saved_then_live = True
                                replacement_ownership_scope = (
                                    ReplacementAlternativeOwnership.UNTRACKED_SOURCE
                                )
                        elif len(payload_lines) > selected_working_line_count and all(
                            payload_lines[index]
                            == _line_body(
                                working_lines[replacement_start + index]
                            )
                            for index in range(selected_working_line_count)
                        ):
                            replacement_owned_prefix_count = (
                                selected_working_line_count
                            )
                            if not no_edge_overlap:
                                replacement_discard_prefix_context_count = (
                                    _matching_discard_prefix_context_count(
                                        payload_lines,
                                        working_lines,
                                        prefix_count=selected_working_line_count,
                                        working_suffix_start=replacement_end,
                                    )
                                )
                                replacement_owned_prefix_count += (
                                    replacement_discard_prefix_context_count
                                )
                        if (
                            replacement_owned_prefix_count is None
                            and selects_explicit_tracked_span
                            and not _replacement_payload_matches_line_span(
                                payload_lines,
                                working_lines,
                                start=replacement_start,
                                end=replacement_end,
                            )
                            and _replacement_payload_retains_selected_addition(
                                line_changes,
                                effective_ids,
                                payload_lines,
                                baseline_lines,
                            )
                        ):
                            replacement_owned_prefix_count = selected_working_line_count
                            materializes_saved_then_live = True
                        if (
                            replacement_owned_prefix_count is None
                            and selected_additions_cover_working_span
                            and (
                                not has_explicit_addition_subspan
                                or selects_added_side_prefix
                            )
                            and baseline_start == baseline_end
                            and _requires_explicit_added_side_alternative(
                                payload_lines,
                                working_lines,
                                working_start=replacement_start,
                                working_end=replacement_end,
                                baseline_file_exists=baseline_file_exists,
                                has_deletion_peer=selected_run_has_deletion,
                                destination_has_file=destination.file_exists,
                                no_edge_overlap=no_edge_overlap,
                            )
                        ):
                            if (
                                not destination.file_exists
                                and source_has_independent_session_edits is None
                            ):
                                if not baseline_file_exists:
                                    snapshot_file_if_untracked(line_changes.path)
                                session_start_lines = source_stack.enter_context(
                                    load_saved_session_file_as_buffer(line_changes.path)
                                )
                                source_has_independent_session_edits = (
                                    not line_sequences_equal(
                                        session_start_lines,
                                        working_lines,
                                    )
                                )
                            replacement_ownership_scope = (
                                ReplacementAlternativeOwnership.EXACT_SAVED_SPAN
                                if (
                                    not payload_lines
                                    or has_explicit_addition_subspan
                                    or source_has_independent_session_edits is True
                                )
                                else (
                                    ReplacementAlternativeOwnership.UNTRACKED_SOURCE
                                    if not baseline_file_exists
                                    else ReplacementAlternativeOwnership.SOURCE_WITHOUT_LIVE
                                )
                            )
                            replacement_owned_prefix_count = selected_working_line_count
                            materializes_saved_then_live = True
                if (
                    uses_explicit_addition_span
                    and replacement_owned_prefix_count is None
                    and not retains_explicit_addition_subspan
                ):
                    effective_ids = (
                        replacement_selection.expand_replacement_selection_ids(
                            line_changes,
                            requested_ids,
                            preserve_partial_addition_prefix=True,
                        )
                    )
                    uses_explicit_addition_span = False
                    continue
                break
            preserve_selected_addition_wording = (
                baseline_start == baseline_end
                and replacement_owned_prefix_count is None
                and _selected_run_has_unselected_deletion(
                    line_changes,
                    effective_ids,
                )
            )
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
                    LineBoundary(baseline_start),
                    LineBoundary(baseline_end),
                ),
                worktree_span=LineSpan(
                    LineBoundary(replacement_start),
                    LineBoundary(replacement_end),
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
                (DisplayLineId(line_id) for line_id in effective_ids),
                view=original_view,
            )
            if materializes_saved_then_live:
                with build_target_working_tree_buffer_with_edit_plan(
                    edit_plan,
                    replacement_payload,
                    working_lines,
                    working_has_trailing_newline=buffer_ends_with_lf(working_lines),
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                ) as live_replacement_buffer:
                    rewritten_working_buffer = LineBuffer.from_chunks(
                        chain(
                            LineRangeView(
                                live_replacement_buffer,
                                0,
                                replacement_start,
                            ),
                            LineRangeView(
                                working_lines,
                                replacement_start,
                                replacement_end,
                            ),
                            LineRangeView(
                                live_replacement_buffer,
                                replacement_start,
                                len(live_replacement_buffer),
                            ),
                        )
                    )
            elif (
                retains_explicit_addition_subspan
                and replacement_owned_prefix_count is None
            ):
                rewritten_working_buffer = build_target_working_tree_buffer_with_edit_plan(
                    edit_plan,
                    replacement_payload,
                    working_lines,
                    working_has_trailing_newline=buffer_ends_with_lf(working_lines),
                    trim_unchanged_edge_anchors=not no_edge_overlap,
                )
            else:
                rewritten_working_buffer = build_target_working_tree_buffer_with_replaced_lines(
                    line_changes,
                    effective_ids,
                    replacement_payload,
                    working_lines,
                    working_has_trailing_newline=buffer_ends_with_lf(working_lines),
                    trim_unchanged_edge_anchors=(
                        not no_edge_overlap
                        and (
                            replacement_owned_prefix_count is None
                            or replacement_discard_prefix_context_count > 0
                        )
                    ),
                    preserved_replacement_prefix_count=(
                        replacement_owned_prefix_count or 0
                    ),
                )
    except ValueError as error:
        exit_with_error(str(error))

    with rewritten_working_buffer as rewritten_working_lines:
        rewritten_snapshot = cast(
            FileSnapshot[RewrittenWorktreeSpace],
            content_snapshot(
                line_changes.path,
                rewritten_working_lines,
                space=RewrittenWorktreeSpace,
            ),
        )
        replacement_new_start, replacement_new_end = (
            _rewritten_replacement_new_range(
                line_changes,
                effective_ids,
                rewritten_working_lines,
                original_working_line_count=original_working_line_count,
                replacement_start=replacement_start,
                replacement_end=replacement_end,
            )
        )
        owned_replacement_new_end = replacement_new_end
        if replacement_owned_prefix_count is not None:
            owned_replacement_new_end = min(
                replacement_new_end,
                replacement_new_start + replacement_owned_prefix_count - 1,
            )
        explicit_alternative_end: int | None = None
        if (
            replacement_owned_prefix_count is not None
            and owned_replacement_new_end < replacement_new_end
        ):
            if materializes_saved_then_live:
                explicit_alternative_end = replacement_new_end
            else:
                with replacement_line_bodies(replacement_payload) as payload_lines:
                    explicit_alternative_end = _verified_explicit_alternative_end(
                        selection_lines=rewritten_working_lines,
                        payload_lines=payload_lines,
                        owned_prefix_count=replacement_owned_prefix_count,
                        alternative_start=owned_replacement_new_end + 1,
                        fallback_end=replacement_new_end,
                    )
        rewritten_cached_lines = _build_rewritten_line_changes(
            line_changes.path,
            rewritten_working_lines,
            rewritten_snapshot=rewritten_snapshot,
            materialized_new_start=(
                replacement_new_start
                if preserve_selected_addition_wording
                else None
            ),
            materialized_new_end=(
                replacement_new_end
                if preserve_selected_addition_wording
                else None
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
            effective_ids,
            rewritten_line_changes,
            replacement_new_start=replacement_new_start,
            replacement_new_end=owned_replacement_new_end,
        )
        mapped_rewritten_ids = _combined_rewritten_selection_ids(
            rewritten_selection_runs,
            additional_ids=LineRanges.empty(),
        )
        rewritten_span_ids = _select_rewritten_span_ids(
            line_changes,
            effective_ids,
            rewritten_line_changes,
            mapped_rewritten_ids=mapped_rewritten_ids,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            replacement_new_start=replacement_new_start,
            replacement_new_end=owned_replacement_new_end,
            addition_target_count=replacement_owned_prefix_count,
        )
        rewritten_selected_ids = _combined_rewritten_selection_ids(
            rewritten_selection_runs,
            additional_ids=rewritten_span_ids,
        )
        selected_rewritten_lines = [
            line
            for line in rewritten_line_changes.lines
            if line.id is not None and line.id in rewritten_selected_ids
        ]
        reanchored_selected_lines = (
            _insertion_references.reanchor_selected_additions_after_relocated_context_boundary(
                rewritten_line_changes.lines,
                selected_rewritten_lines,
                rewritten_working_lines,
            )
        )
        reanchored_by_id = {
            line.id: line
            for line in reanchored_selected_lines
            if line.id is not None
        }
        if reanchored_by_id:
            for line_index, line in enumerate(rewritten_line_changes.lines):
                if line.id in reanchored_by_id:
                    rewritten_line_changes.lines[line_index] = reanchored_by_id[line.id]
            rewritten_selected_ids = rewritten_selected_ids.union(
                LineRanges.from_lines(reanchored_by_id)
            )
        rewritten_worktree_discard_ids = _rewritten_worktree_discard_ids(
            rewritten_selection_runs,
            rewritten_span_ids,
            rewritten_line_changes,
            preserve_selected_additions=preserve_selected_addition_wording,
        )
        rewritten_view = diff_view_identity(
            rewritten_line_changes,
            old_snapshot=baseline_snapshot,
            new_snapshot=snapshot_as_role(
                rewritten_snapshot,
                DiffNewSpace,
            ),
        )
        ownership_selection = resolve_selection(
            rewritten_line_changes,
            (
                DisplayLineId(line_id)
                for line_id in rewritten_selected_ids
            ),
            view=rewritten_view,
        )
        rollback = (
            RollbackSelection(
                resolve_selection(
                    rewritten_line_changes,
                    (
                        DisplayLineId(line_id)
                        for line_id in rewritten_worktree_discard_ids
                    ),
                    view=rewritten_view,
                )
            )
            if rewritten_worktree_discard_ids
            else NoRollback()
        )
        replacement_parent = None
        if replacement_owned_prefix_count is not None and baseline_start < baseline_end:
            replacement_parent = ExplicitReplacementParent(
                baseline=SnapshotSpan(
                    baseline_snapshot_for_plan,
                    LineSpan(
                        LineBoundary(baseline_start),
                        LineBoundary(baseline_end),
                    ),
                ),
                worktree=SnapshotSpan(
                    worktree_snapshot_for_plan,
                    LineSpan(
                        LineBoundary(replacement_start),
                        LineBoundary(replacement_end),
                    ),
                ),
            )
        applied_edit = edit_plan.bind_result(
            rewritten_snapshot,
            replacement_line_count=(replacement_new_end - replacement_new_start + 1),
        )
        replacement_alternatives = None
        if replacement_owned_prefix_count is not None:
            saved_span = SnapshotSpan(
                rewritten_snapshot,
                LineSpan(
                    LineBoundary(replacement_new_start - 1),
                    LineBoundary(owned_replacement_new_end),
                ),
            )
            live_span = (
                SnapshotSpan(
                    rewritten_snapshot,
                    LineSpan(
                        saved_span.span.end,
                        LineBoundary(explicit_alternative_end),
                    ),
                )
                if explicit_alternative_end is not None
                else None
            )
            replacement_alternatives = ExplicitReplacementAlternatives(
                edit=applied_edit,
                saved=saved_span,
                live=live_span,
                parent=replacement_parent,
                ownership_scope=(
                    ReplacementAlternativeOwnership.EXACT_SAVED_SPAN
                    if (
                        replacement_ownership_scope
                        is ReplacementAlternativeOwnership.TRANSLATED_SELECTION
                        and replacement_parent is None
                        and selects_exact_addition_span
                    )
                    else replacement_ownership_scope
                ),
            )
        yield DiscardLineReplacementSelection(
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


def add_discard_line_replacement_to_batch(
    batch_name: str,
    selection: DiscardLineReplacementSelection,
) -> None:
    """Persist a rewritten discard replacement selection to a batch."""
    if selection.destination.batch_name != batch_name:
        raise ValueError("replacement selection belongs to a different batch")
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    metadata = read_batch_metadata(batch_name)
    metadata_revision = BatchMetadataRevision.from_metadata(metadata)
    file_metadata = metadata.get("files", {}).get(selection.file_path)
    if (file_metadata is not None) != selection.destination.file_exists:
        raise ValueError("replacement destination file changed during preparation")

    with ExitStack() as ownership_stack:
        original_working_lines = (
            ownership_stack.enter_context(
                load_working_tree_file_as_buffer(selection.file_path)
            )
            if _selection_may_need_parent_expansion(selection)
            else None
        )
        batch_source_commit: str
        bound_ownership: SourceBoundOwnership
        try:
            alternatives = selection.replacement_alternatives
            if (
                file_metadata is None
                and alternatives is not None
                and alternatives.uses_untracked_source
            ):
                materialized = ownership_stack.enter_context(
                    materialize_untracked_source_replacement(
                        selection.rewritten_working_lines,
                        alternatives,
                    )
                )
                batch_source_commit = create_batch_source_commit(
                    selection.file_path,
                    file_buffer_override=materialized.source_buffer,
                )
                _record_session_batch_source(
                    selection.file_path,
                    batch_source_commit,
                )
                bound_ownership = materialized.bound_ownership
            elif file_metadata is None:
                batch_source_commit = create_batch_source_commit(
                    selection.file_path,
                    file_buffer_override=selection.rewritten_working_lines,
                )
                _record_session_batch_source(
                    selection.file_path,
                    batch_source_commit,
                )
                reference_source_lines = ownership_stack.enter_context(
                    read_git_object_buffer_or_empty(f"HEAD:{selection.file_path}")
                )
                batch_baseline_commit = metadata.get("baseline")
                if not isinstance(batch_baseline_commit, str) or not (
                    batch_baseline_commit
                ):
                    raise ValueError(
                        "replacement update requires a batch baseline commit"
                    )
                reference_target_lines = ownership_stack.enter_context(
                    read_git_object_buffer_or_empty(
                        f"{batch_baseline_commit}:{selection.file_path}"
                    )
                )
                with _acquire_rewritten_source_projection(
                    selection,
                    source_lines=selection.rewritten_working_lines,
                    transform=IdentitySourceCoordinates(),
                ) as source_projection:
                    origin_source_projection = SameContentSpanProjection(
                        selection.transformed_projection.rewritten_snapshot,
                        source_projection.source_snapshot,
                    )
                    ownership = _translate_rewritten_selection_ownership(
                        selection,
                        baseline_lines=reference_source_lines,
                        original_working_lines=original_working_lines,
                        rewritten_lines=selection.rewritten_working_lines,
                        exact_presence_range=(_explicit_owned_prefix_range(selection)),
                        source_projection=source_projection,
                        replacement_origin_source_projection=(
                            origin_source_projection
                        ),
                    )
                    ownership = _expand_source_scoped_alternative_ownership(
                        ownership,
                        selection=selection,
                        source_line_count=len(selection.rewritten_working_lines),
                        alternative_range=_explicit_alternative_range(selection),
                        materialize_source_scope=True,
                    )
                    ownership = _refine_and_preserve_explicit_presence_span_boundary(
                        ownership,
                        selection=selection,
                        baseline_lines=reference_source_lines,
                        source_content_lines=selection.rewritten_working_lines,
                        replacement_origin_source_projection=(
                            origin_source_projection
                        ),
                    )
                translate_ownership_baseline_references(
                    ownership,
                    reference_source_lines,
                    reference_target_lines,
                    replacement_origin_source_lines=reference_source_lines,
                )
                explicit_alternative_range = _explicit_alternative_range(selection)
                if (
                    selection.replacement_alternatives is not None
                    and selection.replacement_alternatives.requires_exact_saved_presence
                    and explicit_alternative_range is not None
                ):
                    explicit_presence_range = _explicit_owned_prefix_range(selection)
                    if explicit_presence_range is None:
                        raise ValueError(
                            "explicit replacement prefix has no source range"
                        )
                    ownership = _add_explicit_source_alternative_replacement(
                        ownership,
                        selection=selection,
                        presence_range=explicit_presence_range,
                        alternative_range=explicit_alternative_range,
                    )
                bound_ownership = SourceBoundOwnership(
                    content_snapshot(
                        selection.file_path,
                        selection.rewritten_working_lines,
                        space=BatchSourceSpace,
                    ),
                    ownership,
                )
            else:
                bound_ownership, batch_source_commit = _merge_replacement_with_batch(
                    selection,
                    file_metadata=file_metadata,
                    batch_baseline_commit=metadata.get("baseline"),
                    original_working_lines=original_working_lines,
                    ownership_stack=ownership_stack,
                )
        except ValueError as e:
            exit_with_error(
                _(
                    "Cannot discard lines to batch: batch source is stale and remapping failed.\n"
                    "File: {file}\n"
                    "Batch: {batch}\n"
                    "Error: {error}"
                ).format(
                    file=display_path(selection.file_path),
                    batch=batch_name,
                    error=str(e),
                )
            )

        snapshot_file_if_untracked(selection.file_path)
        add_source_bound_file_to_batch(
            batch_name,
            selection.file_path,
            bound_ownership,
            detect_file_mode(selection.file_path),
            batch_source_commit=batch_source_commit,
            expected_metadata_revision=metadata_revision,
        )


def _merge_replacement_with_batch(
    selection: DiscardLineReplacementSelection,
    *,
    file_metadata: BatchFileMetadataDict,
    batch_baseline_commit: str | None,
    original_working_lines: LineBuffer | None,
    ownership_stack: ExitStack,
) -> tuple[SourceBoundOwnership, str]:
    if not isinstance(batch_baseline_commit, str) or not batch_baseline_commit:
        raise ValueError("replacement update requires a batch baseline commit")

    current_batch_source = file_metadata.get("batch_source_commit")
    existing_ownership = ownership_stack.enter_context(
        acquire_ownership_for_metadata_dict(file_metadata)
    )
    old_source_buffer = read_git_object_buffer_or_none(
        f"{current_batch_source}:{selection.file_path}"
    )
    if old_source_buffer is None:
        exit_with_error(
            _(
                "Cannot discard lines to batch: failed to read batch source for '{file}'."
            ).format(file=display_path(selection.file_path))
        )

    reference_source_lines = ownership_stack.enter_context(
        read_git_object_buffer_or_empty(f"HEAD:{selection.file_path}")
    )
    reference_target_lines = ownership_stack.enter_context(
        read_git_object_buffer_or_empty(
            f"{batch_baseline_commit}:{selection.file_path}"
        )
    )
    with old_source_buffer as old_source_lines:
        old_bound_ownership = SourceBoundOwnership(
            content_snapshot(
                selection.file_path,
                old_source_lines,
                space=BatchSourceSpace,
            ),
            existing_ownership,
        )
        refreshed_complete = refresh_complete_source_replacement(
            old_source_lines,
            old_bound_ownership,
            rewritten_lines=selection.rewritten_working_lines,
            alternatives=selection.replacement_alternatives,
        )
        if refreshed_complete is not None:
            refreshed_complete = ownership_stack.enter_context(refreshed_complete)
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=refreshed_complete.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return refreshed_complete.bound_ownership, batch_source_commit

        promoted_complete = promote_untracked_presence_to_complete_source_replacement(
            old_source_lines,
            old_bound_ownership,
            rewritten_lines=selection.rewritten_working_lines,
            alternatives=selection.replacement_alternatives,
        )
        if promoted_complete is not None:
            promoted_complete = ownership_stack.enter_context(promoted_complete)
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=promoted_complete.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return promoted_complete.bound_ownership, batch_source_commit

        with (
            advance_source_lines_preserving_existing_presence(
                old_lines=old_source_lines,
                working_lines=selection.rewritten_working_lines,
                ownership=existing_ownership,
                advancing_working_ranges=(
                    selection.rewritten_selected_ids
                    if selection.replacement_alternatives is not None
                    else None
                ),
                advancing_alternatives=selection.replacement_alternatives,
            ) as source_with_provenance,
        ):
            remapped_existing_ownership = remap_batch_ownership_with_lineage(
                ownership=existing_ownership,
                lineage=source_with_provenance.lineage,
            )
            with _acquire_rewritten_source_projection(
                selection,
                source_lines=source_with_provenance.source_buffer,
                transform=ExactLineageSourceCoordinates(
                    source_with_provenance.lineage
                ),
            ) as source_projection:
                origin_source_projection = (
                    BatchSourceExactTransform.from_rewritten_working_lineage(
                        selection.transformed_projection.rewritten_snapshot,
                        source_projection.source_snapshot,
                        source_with_provenance.lineage,
                    )
                )
                exact_prefix_range = _exact_owned_prefix_source_range(
                    selection,
                    source_with_provenance.source_buffer,
                    translate_working_range=(
                        source_with_provenance.lineage.translate_working_range
                    ),
                )
                exact_alternative_range = _exact_alternative_source_range(
                    selection,
                    source_with_provenance.source_buffer,
                    translate_working_range=(
                        source_with_provenance.lineage.translate_working_range
                    ),
                )
                if (
                    selection.replacement_alternatives is not None
                    and selection.replacement_alternatives.requires_exact_saved_presence
                    and (
                    exact_prefix_range is None
                    or (
                        _explicit_alternative_range(selection) is not None
                        and exact_alternative_range is None
                    )
                    )
                ):
                    raise ValueError(
                        "advanced batch source does not preserve the replacement "
                        "alternatives as contiguous ranges"
                    )
                new_ownership = _translate_rewritten_selection_ownership(
                    selection,
                    baseline_lines=reference_source_lines,
                    original_working_lines=original_working_lines,
                    rewritten_lines=selection.rewritten_working_lines,
                    exact_presence_range=exact_prefix_range,
                    source_projection=source_projection,
                    replacement_origin_source_projection=(
                        origin_source_projection
                    ),
                )
                new_ownership = _expand_source_scoped_alternative_ownership(
                    new_ownership,
                    selection=selection,
                    source_line_count=len(source_with_provenance.source_buffer),
                    alternative_range=exact_alternative_range,
                    materialize_source_scope=False,
                )
                new_ownership = _refine_and_preserve_explicit_presence_span_boundary(
                    new_ownership,
                    selection=selection,
                    baseline_lines=reference_source_lines,
                    source_content_lines=source_with_provenance.source_buffer,
                    replacement_origin_source_projection=(
                        origin_source_projection
                    ),
                )
            translate_ownership_baseline_references(
                new_ownership,
                reference_source_lines,
                reference_target_lines,
                replacement_origin_source_lines=reference_source_lines,
            )
            if (
                selection.replacement_alternatives is not None
                and selection.replacement_alternatives.requires_exact_saved_presence
                and exact_prefix_range is not None
                and exact_alternative_range is not None
            ):
                new_ownership = _add_explicit_source_alternative_replacement(
                    new_ownership,
                    selection=selection,
                    presence_range=exact_prefix_range,
                    alternative_range=exact_alternative_range,
                )
            batch_source_commit = create_batch_source_commit(
                selection.file_path,
                file_buffer_override=source_with_provenance.source_buffer,
            )
            _record_session_batch_source(selection.file_path, batch_source_commit)
            return (
                SourceBoundOwnership(
                    content_snapshot(
                        selection.file_path,
                        source_with_provenance.source_buffer,
                        space=BatchSourceSpace,
                    ),
                    merge_batch_ownership(
                        remapped_existing_ownership,
                        new_ownership,
                    ),
                ),
                batch_source_commit,
            )


def _record_session_batch_source(file_path: str, batch_source_commit: str) -> None:
    batch_sources = load_session_batch_sources()
    batch_sources[file_path] = batch_source_commit
    save_session_batch_sources(batch_sources)


def _refine_presence_references_from_source_content(
    ownership: BatchOwnership,
    source_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> None:
    normalized_baseline = normalize_line_sequence_endings(baseline_lines)

    baseline_positions: dict[bytes, list[int]] = {}
    for bl_idx, bl_content in enumerate(normalized_baseline):
        baseline_positions.setdefault(bl_content, []).append(bl_idx)

    for claim in ownership.presence_claims:
        for source_line in list(claim.baseline_references):
            if source_line <= 1:
                continue
            ref = claim.baseline_references[source_line]
            current_after = ref.after_line or 0

            preceding_content = normalize_line_sequence_endings(
                [bytes(source_lines[source_line - 2])]
            )[0]
            if not normalized_line_payload(preceding_content):
                continue

            positions = baseline_positions.get(preceding_content)
            if positions is None:
                continue

            insert_point = bisect_left(positions, current_after)
            if insert_point >= len(positions):
                continue
            best_match = positions[insert_point] + 1

            if best_match <= current_after:
                continue

            position = best_match
            new_after = position or None
            new_before = (
                position + 1 if position < len(baseline_lines) else None
            )
            claim.baseline_references[source_line] = BaselineReference(
                after_line=new_after,
                after_content=(
                    bytes(baseline_lines[new_after - 1])
                    if new_after is not None
                    else None
                ),
                has_after_line=True,
                before_line=new_before,
                before_content=(
                    bytes(baseline_lines[new_before - 1])
                    if new_before is not None
                    else None
                ),
                has_before_line=True,
            )


@contextmanager
def _acquire_rewritten_source_projection(
    selection: DiscardLineReplacementSelection,
    *,
    source_lines: LineBuffer,
    transform: SourceCoordinateTransform,
) -> Iterator[SourceCoordinateProjection]:
    """Build an immutable selected-row projection into one exact source."""
    coordinate_lines = selection.rewritten_line_changes.lines
    source_snapshot = cast(
        FileSnapshot[BatchSourceSpace],
        content_snapshot(
            selection.file_path,
            source_lines,
            space=BatchSourceSpace,
        ),
    )

    def projected_pairs() -> Iterator[tuple[DisplayLineId, int | None]]:
        for line, source_line in translate_display_source_coordinates(
            coordinate_lines,
            transform,
        ):
            if line.id is None:
                continue
            yield DisplayLineId(line.id), source_line

    def resolve_anonymous_row(new_line_number: int | None) -> int | None:
        if new_line_number is None:
            return None
        return transform.translate_working_line(new_line_number)

    with SourceCoordinateProjection.from_pairs(
        view_identity=(
            selection.transformed_projection.ownership_selection.view.renderer_identity
        ),
        source_snapshot=source_snapshot,
        pairs=projected_pairs(),
        capacity=len(coordinate_lines),
        anonymous_row_resolver=resolve_anonymous_row,
    ) as projection:
        yield projection


def _translate_rewritten_selection_ownership(
    selection: DiscardLineReplacementSelection,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer | None,
    rewritten_lines: LineBuffer,
    exact_presence_range: tuple[int, int] | None = None,
    source_projection: SourceCoordinateProjection,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Translate the selected rewritten rows with full-hunk provenance."""
    source_projection.require_view(
        selection.transformed_projection.ownership_selection.view.renderer_identity
    )
    expanded_parents = _expanded_selected_replacement_parents(
        selection,
        baseline_lines=baseline_lines,
        original_working_lines=original_working_lines,
    )
    expanded_deletion_ids = LineRanges.from_ranges(
        range_pair
        for parent in expanded_parents
        for range_pair in parent.rewritten_deletion_ids.ranges()
    )
    selected_ids = selection.rewritten_selected_ids.difference(expanded_deletion_ids)
    replacement_runs = stream_replacement_line_runs_from_lines(
        old_file_lines=baseline_lines,
        new_file_lines=rewritten_lines,
    )
    ownership = translate_hunk_selection_to_batch_ownership(
        selection.rewritten_line_changes.lines,
        selected_ids,
        replacement_line_runs=replacement_runs,
        replacement_origin=SameStreamReplacementOrigin(baseline_lines),
        baseline_lines=baseline_lines,
        source_projection=source_projection,
        replacement_origin_source_projection=(
            replacement_origin_source_projection
        ),
    )
    ownership = _add_expanded_replacement_parents(
        ownership,
        selection=selection,
        expanded_parents=expanded_parents,
        baseline_lines=baseline_lines,
        source_projection=source_projection,
        replacement_origin_source_projection=(
            replacement_origin_source_projection
        ),
    )
    if (
        selection.replacement_alternatives is not None
        and selection.replacement_alternatives.requires_exact_saved_presence
        and (
        exact_presence_range is None
        or ownership.deletions
        or ownership.replacement_units
        )
    ):
        raise ValueError(
            "exact addition prefix did not translate to presence-only ownership"
        )
    if exact_presence_range is not None and (
        (
            selection.replacement_alternatives is not None
            and selection.replacement_alternatives.requires_exact_saved_presence
        )
        or (not ownership.deletions and not ownership.replacement_units)
    ):
        exact_presence_lines = LineRanges.from_ranges((exact_presence_range,))
        if ownership.presence_line_set() != exact_presence_lines:
            ownership.presence_claims = presence_claims_from_source_lines(
                exact_presence_lines,
                ownership.presence_baseline_references(),
            )
    return ownership


def _expand_source_scoped_alternative_ownership(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    source_line_count: int,
    alternative_range: tuple[int, int] | None,
    materialize_source_scope: bool,
) -> BatchOwnership:
    """Select every source line except the version left in the worktree."""
    alternatives = selection.replacement_alternatives
    if (
        alternatives is None
        or not alternatives.owns_source_without_live
        or not materialize_source_scope
    ):
        return ownership
    if alternative_range is None:
        raise ValueError("source-scoped replacement has no live source range")
    if ownership.deletions or ownership.replacement_units:
        raise ValueError("source-scoped replacement did not translate to presence")
    alternative_start, alternative_end = alternative_range
    if not 1 <= alternative_start <= alternative_end <= source_line_count:
        raise ValueError("source-scoped replacement has an invalid live range")

    owned_builder = LineRangeBuilder()
    if alternative_start > 1:
        owned_builder.add_range(1, alternative_start - 1)
    if alternative_end < source_line_count:
        owned_builder.add_range(alternative_end + 1, source_line_count)
    ownership.presence_claims = presence_claims_from_source_lines(
        owned_builder.finish(),
        {},
    )
    return ownership


def _refine_and_preserve_explicit_presence_span_boundary(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    baseline_lines: Sequence[bytes],
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Update references to nearby lines without moving the saved text."""
    source_span = _explicit_presence_source_span(
        ownership,
        selection=selection,
        source_content_lines=source_content_lines,
        replacement_origin_source_projection=(
            replacement_origin_source_projection
        ),
    )
    initial_reference = (
        _effective_presence_reference_for_line(
            ownership,
            source_span.span.start.offset + 1,
        )
        if source_span is not None
        else None
    )
    _refine_presence_references_from_source_content(
        ownership,
        source_content_lines,
        baseline_lines,
    )
    if source_span is None:
        return ownership
    with MatcherWorkspace() as workspace:
        return _preserve_explicit_presence_span_boundary(
            workspace,
            ownership,
            selection=selection,
            initial_reference=initial_reference,
            source_span=source_span,
            baseline_lines=baseline_lines,
            source_content_lines=source_content_lines,
            replacement_origin_source_projection=(
                replacement_origin_source_projection
            ),
        )


def _explicit_presence_source_span(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> SnapshotSpan[BatchSourceSpace] | None:
    """Find the replacement in the batch source."""
    if ownership.deletions or ownership.replacement_units:
        return None
    explicit_edit_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        selection.transformed_projection.explicit_edit.rewritten_span,
    )
    alternatives = selection.replacement_alternatives
    owned_prefix_span = alternatives.saved.span if alternatives is not None else None
    presence_span = explicit_edit_span.span
    if (
        alternatives is not None
        and alternatives.requires_exact_saved_presence
        and owned_prefix_span is not None
        and len(owned_prefix_span) > 1
    ):
        presence_span = owned_prefix_span
    rewritten_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        presence_span,
    )
    source_span = replacement_origin_source_projection.translate_span(rewritten_span)
    if source_span is None:
        source_span = _infer_explicit_source_span_from_owned_prefix(
            rewritten_span,
            rewritten_lines=selection.rewritten_working_lines,
            source_lines=source_content_lines,
            owned_presence=ownership.presence_line_set(),
            target_snapshot=(
                replacement_origin_source_projection.target_snapshot
            ),
        )
    if source_span is None or len(source_span.span) == 0:
        return None
    return source_span


def _effective_presence_reference_for_line(
    ownership: BatchOwnership,
    source_line: int,
) -> BaselineReference | None:
    """Return the latest saved location for one source line."""
    for claim in reversed(ownership.presence_claims):
        reference = claim.baseline_references.get(source_line)
        if reference is not None:
            return reference
    return None


def _preserve_explicit_presence_span_boundary(
    workspace: MatcherWorkspace,
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    initial_reference: BaselineReference | None,
    source_span: SnapshotSpan[BatchSourceSpace],
    baseline_lines: Sequence[bytes],
    source_content_lines: Sequence[bytes],
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Give all newly saved lines one shared insertion point."""
    explicit_edit_span = SnapshotSpan(
        selection.transformed_projection.rewritten_snapshot,
        selection.transformed_projection.explicit_edit.rewritten_span,
    )
    alternatives = selection.replacement_alternatives
    boundary_search_span = (
        alternatives.boundary_search_span
        if alternatives is not None
        else explicit_edit_span
    )
    following_boundary_span = replacement_origin_source_projection.translate_span(
        boundary_search_span
    )
    presence_lines = LineRanges.from_ranges(
        (
            (
                source_span.span.start.offset + 1,
                source_span.span.end.offset,
            ),
        )
    )
    owned_presence = ownership.presence_line_set()
    if owned_presence != presence_lines and not (
        _presence_is_explicit_span_prefix_with_blank_suffix(
            owned_presence,
            explicit_presence=presence_lines,
            source_lines=source_content_lines,
        )
    ):
        return ownership

    references = EffectivePresenceReferenceIndex(workspace, ownership)
    source_occurrences = LinePayloadOccurrenceIndex(
        workspace,
        source_content_lines,
    )
    first_source_line = source_span.span.start.offset + 1
    following_boundary = (
        following_boundary_span.span.end.offset
        if following_boundary_span is not None
        else source_span.span.end.offset
    )
    refined_first_reference = references.reference_for(first_source_line)
    shared_reference = (
        (
            initial_reference
            if initial_reference == refined_first_reference
            else (
                _nearest_following_explicit_boundary(
                    (initial_reference,),
                    source_occurrences=source_occurrences,
                    start_boundary=following_boundary,
                )
                or _boundary_before_reference_after(
                    initial_reference,
                    baseline_lines,
                )
            )
        )
        if len(source_span.span) == 1 and initial_reference is not None
        else _nearest_following_explicit_boundary(
            (
                reference
                for _source_line, reference in references.items()
                if reference is not None
            ),
            source_occurrences=source_occurrences,
            start_boundary=following_boundary,
        )
    ) or refined_first_reference
    if not ownership.presence_claims:
        return ownership
    canonical_claim = ownership.presence_claims[0]
    ownership.presence_claims[:] = [canonical_claim]
    canonical_claim.source_lines = presence_lines.to_range_strings()
    canonical_claim.baseline_references.clear()
    if shared_reference is not None:
        for source_line in range(
            first_source_line,
            source_span.span.end.offset + 1,
        ):
            canonical_claim.baseline_references[source_line] = shared_reference
    return ownership


def _infer_explicit_source_span_from_owned_prefix(
    rewritten_span: SnapshotSpan[RewrittenWorktreeSpace],
    *,
    rewritten_lines: Sequence[bytes],
    source_lines: Sequence[bytes],
    owned_presence: LineRanges,
    target_snapshot: FileSnapshot[BatchSourceSpace],
) -> SnapshotSpan[BatchSourceSpace] | None:
    """Find the rewritten span from its owned leading lines."""
    owned_ranges = owned_presence.ranges()
    rewritten_count = len(rewritten_span.span)
    if len(owned_ranges) != 1 or rewritten_count == 0:
        return None
    owned_start, owned_end = owned_ranges[0]
    owned_count = owned_end - owned_start + 1
    source_end = owned_start + rewritten_count - 1
    if owned_count > rewritten_count or source_end > len(source_lines):
        return None
    rewritten_view = LineRangeView(
        rewritten_lines,
        rewritten_span.span.start.offset,
        rewritten_span.span.end.offset,
    )
    if not line_slice_equals(source_lines, owned_start - 1, rewritten_view):
        return None
    return SnapshotSpan(
        target_snapshot,
        LineSpan(
            LineBoundary(owned_start - 1),
            LineBoundary(source_end),
        ),
    )


def _presence_is_explicit_span_prefix_with_blank_suffix(
    owned_presence: LineRanges,
    *,
    explicit_presence: LineRanges,
    source_lines: Sequence[bytes],
) -> bool:
    """Return whether the only unowned following lines are blank."""
    owned_ranges = owned_presence.ranges()
    explicit_ranges = explicit_presence.ranges()
    if len(owned_ranges) != 1 or len(explicit_ranges) != 1:
        return False
    owned_start, owned_end = owned_ranges[0]
    explicit_start, explicit_end = explicit_ranges[0]
    if (
        owned_start != explicit_start
        or owned_end >= explicit_end
        or explicit_end > len(source_lines)
    ):
        return False
    return all(
        not normalized_line_payload(source_lines[line_number - 1])
        for line_number in range(owned_end + 1, explicit_end + 1)
    )


def _nearest_following_explicit_boundary(
    references: Iterable[BaselineReference],
    *,
    source_occurrences: LinePayloadOccurrenceIndex,
    start_boundary: int,
) -> BaselineReference | None:
    """Find the nearest saved boundary after the selection."""
    best_position: int | None = None
    best_reference: BaselineReference | None = None
    for reference in references:
        if reference.after_content is None or reference.before_content is None:
            continue
        position = source_occurrences.first_adjacent_boundary_position(
            reference.after_content,
            reference.before_content,
            start_position=max(start_boundary, 1),
        )
        if position is not None and (best_position is None or position < best_position):
            best_position = position
            best_reference = reference
            if position == max(start_boundary, 1):
                break
    return best_reference


def _boundary_before_reference_after(
    reference: BaselineReference,
    baseline_lines: Sequence[bytes],
) -> BaselineReference | None:
    """Move an insertion from after a line to just before it."""
    before_line = reference.after_line
    if before_line is None or before_line > len(baseline_lines):
        return None
    after_line = before_line - 1 or None
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(baseline_lines[after_line - 1]) if after_line is not None else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=bytes(baseline_lines[before_line - 1]),
        has_before_line=True,
    )


def _exact_owned_prefix_source_range(
    selection: DiscardLineReplacementSelection,
    source_lines: LineBuffer,
    *,
    translate_working_range: Callable[
        [int, int],
        tuple[int, int] | None,
    ],
) -> tuple[int, int] | None:
    """Translate and verify a preserved prefix in the advanced source."""
    prefix_range = _explicit_owned_prefix_range(selection)
    if prefix_range is None:
        return None
    prefix_start, prefix_end = prefix_range
    source_range = translate_working_range(prefix_start, prefix_end)
    if source_range is None:
        return None
    source_start, source_end = source_range
    prefix_lines = LineRangeView(
        selection.rewritten_working_lines,
        prefix_start - 1,
        prefix_end,
    )
    if not line_slice_equals(source_lines, source_start - 1, prefix_lines):
        return None
    return source_start, source_end


def _exact_alternative_source_range(
    selection: DiscardLineReplacementSelection,
    source_lines: LineBuffer,
    *,
    translate_working_range: Callable[
        [int, int],
        tuple[int, int] | None,
    ],
) -> tuple[int, int] | None:
    """Translate and verify the live alternative in the advanced source."""
    alternative_range = _explicit_alternative_range(selection)
    if alternative_range is None:
        return None
    alternative_start, alternative_end = alternative_range
    source_range = translate_working_range(
        alternative_start,
        alternative_end,
    )
    if source_range is None:
        return None
    source_start, source_end = source_range
    alternative_lines = LineRangeView(
        selection.rewritten_working_lines,
        alternative_start - 1,
        alternative_end,
    )
    if not line_slice_equals(source_lines, source_start - 1, alternative_lines):
        return None
    return source_start, source_end


def _explicit_owned_prefix_range(
    selection: DiscardLineReplacementSelection,
) -> tuple[int, int] | None:
    """Return the preserved prefix's one-based rewritten source range."""
    alternatives = selection.replacement_alternatives
    return alternatives.saved_range if alternatives is not None else None


def _explicit_alternative_range(
    selection: DiscardLineReplacementSelection,
) -> tuple[int, int] | None:
    """Return the rewritten range retained as the live alternative."""
    alternatives = selection.replacement_alternatives
    return alternatives.live_range if alternatives is not None else None


def _explicit_source_alternative_reference(
    selection: DiscardLineReplacementSelection,
) -> BaselineReference:
    """Describe the live alternative after the owned prefix is removed."""
    prefix_range = _explicit_owned_prefix_range(selection)
    alternative_range = _explicit_alternative_range(selection)
    if prefix_range is None or alternative_range is None:
        raise ValueError("explicit source alternative has incomplete coordinates")

    prefix_start, prefix_end = prefix_range
    alternative_start, alternative_end = alternative_range
    if alternative_start != prefix_end + 1:
        raise ValueError("explicit source alternatives are not contiguous")

    prefix_line_count = prefix_end - prefix_start + 1
    target_alternative_start = alternative_start - prefix_line_count
    target_alternative_end = alternative_end - prefix_line_count
    after_line = target_alternative_start - 1 if target_alternative_start > 1 else None
    before_line = (
        target_alternative_end + 1
        if alternative_end < len(selection.rewritten_working_lines)
        else None
    )
    return BaselineReference(
        after_line=after_line,
        after_content=(
            bytes(selection.rewritten_working_lines[prefix_start - 2])
            if after_line is not None
            else None
        ),
        has_after_line=True,
        before_line=before_line,
        before_content=(
            bytes(selection.rewritten_working_lines[alternative_end])
            if before_line is not None
            else None
        ),
        has_before_line=True,
    )


def _add_explicit_source_alternative_replacement(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    presence_range: tuple[int, int],
    alternative_range: tuple[int, int],
) -> BatchOwnership:
    """Couple an owned explicit prefix to its retained source alternative."""
    presence_start, presence_end = presence_range
    alternative_start, alternative_end = alternative_range
    explicit_alternative_range = _explicit_alternative_range(selection)
    if explicit_alternative_range is None:
        raise ValueError("explicit source alternative has no rewritten range")
    explicit_alternative_start, explicit_alternative_end = explicit_alternative_range
    if (
        alternative_start != presence_end + 1
        or alternative_end - alternative_start
        != explicit_alternative_end - explicit_alternative_start
    ):
        raise ValueError(
            "advanced batch source split the explicit replacement alternatives"
        )

    deletions = list(ownership.deletions)
    deletions.append(
        AbsenceClaim(
            anchor_line=presence_start - 1 if presence_start > 1 else None,
            content_lines=LineRangeView(
                selection.rewritten_working_lines,
                explicit_alternative_start - 1,
                explicit_alternative_end,
            ),
            baseline_reference=_explicit_source_alternative_reference(selection),
            source_alternative=True,
        )
    )
    replacement_units = list(ownership.replacement_units)
    replacement_units.append(
        ReplacementUnit(
            presence_lines=(
                LineRanges.from_ranges((presence_range,)).to_range_strings()
            ),
            deletion_indices=[len(deletions) - 1],
        )
    )
    return BatchOwnership(
        presence_claims=ownership.presence_claims,
        deletions=deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(deletions),
        ),
    )


def _rewritten_replacement_new_range(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    rewritten_lines: LineBuffer,
    *,
    original_working_line_count: int,
    replacement_start: int,
    replacement_end: int,
) -> tuple[int, int]:
    """Return the rewritten-file line range occupied by replacement payload."""
    if not any(
        line.id is not None and line.id in selected_ids for line in line_changes.lines
    ):
        raise ValueError("replacement selection has no file coordinates")
    replacement_line_count = (
        len(rewritten_lines)
        - original_working_line_count
        + replacement_end
        - replacement_start
    )
    return (
        replacement_start + 1,
        replacement_start + max(replacement_line_count, 0),
    )


def _build_rewritten_line_changes(
    path: str,
    rewritten_lines: LineBuffer,
    *,
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace],
    materialized_new_start: int | None,
    materialized_new_end: int | None,
) -> LineLevelChange | None:
    """Render a rewritten diff while retaining explicit insertion provenance.

    Git may align replacement text with a deleted baseline line and render the
    new occurrence as unchanged context. Mask an explicitly retained span
    while diffing, then restore its real bytes in the rendered addition rows so
    ownership can still select the occurrence at its rewritten coordinate.
    """
    line_changes = build_file_hunk_from_buffer(path, rewritten_lines)
    if materialized_new_start is None and materialized_new_end is None:
        return line_changes
    if materialized_new_start is None or materialized_new_end is None:
        raise ValueError("materialized replacement span is incomplete")
    if materialized_new_start > materialized_new_end:
        return line_changes
    if materialized_new_start < 1 or materialized_new_end > len(rewritten_lines):
        raise ValueError("materialized replacement span exceeds rewritten file")
    if line_changes is not None and _rewritten_span_is_additions(
        line_changes,
        start=materialized_new_start,
        end=materialized_new_end,
    ):
        return line_changes

    with read_git_object_buffer_or_empty(f"HEAD:{path}") as baseline_lines:
        mask_prefix = _unique_replacement_mask_prefix(
            rewritten_snapshot,
            baseline_lines,
            rewritten_lines,
        )
        with LineBuffer.from_chunks(
            _masked_replacement_chunks(
                rewritten_lines,
                start=materialized_new_start,
                end=materialized_new_end,
                mask_prefix=mask_prefix,
            )
        ) as masked_lines:
            line_changes = build_file_hunk_from_buffer(path, masked_lines)

    if line_changes is None:
        return None
    _restore_masked_replacement_rows(
        line_changes,
        rewritten_lines,
        start=materialized_new_start,
        end=materialized_new_end,
        mask_prefix=mask_prefix,
    )
    return line_changes


def _rewritten_span_is_additions(
    line_changes: LineLevelChange,
    *,
    start: int,
    end: int,
) -> bool:
    """Return whether every rewritten line in a span has an addition row."""
    expected_new_line = start
    for line in line_changes.lines:
        new_line_number = line.new_line_number
        if new_line_number is None or new_line_number < start:
            continue
        if new_line_number > end:
            break
        if line.kind != "+" or new_line_number != expected_new_line:
            return False
        expected_new_line += 1
    return expected_new_line == end + 1


def _unique_replacement_mask_prefix(
    rewritten_snapshot: FileSnapshot[RewrittenWorktreeSpace],
    baseline_lines: Sequence[bytes],
    rewritten_lines: Sequence[bytes],
) -> bytes:
    """Return a line prefix absent from both real diff endpoints."""
    stem = (
        b"git-stage-batch:explicit-replacement-mask:"
        + rewritten_snapshot.identity.value.encode("utf-8")
        + b":"
    )
    attempt = 0
    while True:
        candidate = stem + str(attempt).encode("ascii") + b":"
        if not any(
            _line_body(line).startswith(candidate)
            for lines in (baseline_lines, rewritten_lines)
            for line in lines
        ):
            return candidate
        attempt += 1


def _masked_replacement_chunks(
    rewritten_lines: Sequence[bytes],
    *,
    start: int,
    end: int,
    mask_prefix: bytes,
) -> Iterator[bytes]:
    """Yield rewritten bytes with one one-based line span made distinctive."""
    yield from LineRangeView(rewritten_lines, 0, start - 1)
    for new_line_number in range(start, end + 1):
        original_line = rewritten_lines[new_line_number - 1]
        yield (
            mask_prefix
            + str(new_line_number).encode("ascii")
            + (b"\n" if original_line.endswith(b"\n") else b"")
        )
    yield from LineRangeView(rewritten_lines, end, len(rewritten_lines))


def _restore_masked_replacement_rows(
    line_changes: LineLevelChange,
    rewritten_lines: Sequence[bytes],
    *,
    start: int,
    end: int,
    mask_prefix: bytes,
) -> None:
    """Restore real bytes in the materialized rewritten addition rows."""
    expected_new_line = start
    for index, line in enumerate(line_changes.lines):
        if line.kind != "+" or not line.text_bytes.startswith(mask_prefix):
            continue
        suffix = line.text_bytes[len(mask_prefix) :]
        if not suffix.isdigit():
            raise ValueError("materialized replacement mask has an invalid line")
        new_line_number = int(suffix)
        if (
            new_line_number != expected_new_line
            or line.new_line_number != new_line_number
        ):
            raise ValueError("materialized replacement rows are out of order")
        original_line = rewritten_lines[new_line_number - 1]
        line_changes.lines[index] = replace(
            line,
            text_bytes=(
                original_line[:-1]
                if original_line.endswith(b"\n")
                else original_line
            ),
            has_trailing_newline=original_line.endswith(b"\n"),
        )
        expected_new_line += 1
    if expected_new_line != end + 1:
        raise ValueError("materialized replacement rows are missing from the diff")


def _line_body(line: bytes) -> bytes:
    """Return one line without its source line ending."""
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


def _requires_explicit_added_side_alternative(
    replacement_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    working_start: int,
    working_end: int,
    baseline_file_exists: bool,
    has_deletion_peer: bool,
    destination_has_file: bool,
    no_edge_overlap: bool,
) -> bool:
    """Check whether the source must store both versions of the text.

    A shorter replacement always needs both. A longer replacement needs both
    unless it begins with all selected text. For equal line counts, keep the
    older behavior only when each new line still contains its selected text.
    """
    working_count = working_end - working_start
    replacement_count = len(replacement_lines)
    if replacement_count == 0:
        return working_count > 0
    if replacement_count > working_count:
        if baseline_file_exists or has_deletion_peer:
            return False
        return any(
            replacement_lines[index] != _line_body(working_lines[working_start + index])
            for index in range(working_count)
        )
    if replacement_count < working_count:
        return True
    if has_deletion_peer:
        return False
    for index in range(working_count):
        replacement_line = replacement_lines[index]
        working_line = _line_body(working_lines[working_start + index])
        if replacement_line == working_line:
            continue
        if no_edge_overlap:
            return True
        if replacement_line not in working_line and (
            destination_has_file or not baseline_file_exists
        ):
            return True
    return False


def _replacement_payload_matches_line_span(
    replacement_lines: Sequence[bytes],
    lines: Sequence[bytes],
    *,
    start: int,
    end: int,
) -> bool:
    """Return whether the payload equals one span, ignoring line endings."""
    return len(replacement_lines) == end - start and all(
        replacement_lines[offset] == _line_body(lines[start + offset])
        for offset in range(len(replacement_lines))
    )


def _replacement_payload_retains_selected_addition(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    replacement_lines: Sequence[bytes],
    baseline_lines: Sequence[bytes],
) -> bool:
    """Check whether the replacement keeps selected text absent from the old file.

    This replacement is the version to leave in the worktree, not just new text
    for the batch. Store both versions so undo cannot overwrite it with the old
    file. For large files, keep the indexes in temporary mapped files instead
    of Python objects for every line.
    """
    with MatcherWorkspace() as workspace:
        replacement_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            replacement_lines,
            ignore_indentation=True,
        )
        baseline_occurrences: LinePayloadOccurrenceIndex | None = None
        for line in line_changes.lines:
            if line.kind != "+" or line.id is None or line.id not in selected_ids:
                continue
            content = normalized_line_payload(line.text_bytes)
            if (
                _line_is_delimiter_only(content)
                or replacement_occurrences.occurrence_count(content) == 0
            ):
                continue
            if baseline_occurrences is None:
                baseline_occurrences = LinePayloadOccurrenceIndex(
                    workspace,
                    baseline_lines,
                    ignore_indentation=True,
                )
            if baseline_occurrences.occurrence_count(content) == 0:
                return True
    return False


def _matching_discard_prefix_context_count(
    payload_lines: Sequence[bytes],
    working_lines: Sequence[bytes],
    *,
    prefix_count: int,
    working_suffix_start: int,
) -> int:
    """Count copied delimiter context discarded with an owned prefix."""
    payload_index = prefix_count
    working_index = working_suffix_start
    matched = 0
    while payload_index < len(payload_lines) - 1 and working_index < len(working_lines):
        payload_line = payload_lines[payload_index]
        if (
            not payload_line.strip()
            or not _line_is_delimiter_only(payload_line)
            or payload_line != _line_body(working_lines[working_index])
        ):
            break
        matched += 1
        payload_index += 1
        working_index += 1
    return matched


def _verified_explicit_alternative_end(
    *,
    selection_lines: Sequence[bytes],
    payload_lines: Sequence[bytes],
    owned_prefix_count: int,
    alternative_start: int,
    fallback_end: int,
) -> int:
    """Extend the live version across following text when it matches."""
    alternative_count = len(payload_lines) - owned_prefix_count
    if alternative_count <= 0:
        return fallback_end
    candidate_end = alternative_start + alternative_count - 1
    if candidate_end > len(selection_lines):
        return fallback_end
    if all(
        _line_body(selection_lines[alternative_start + offset - 1])
        == payload_lines[owned_prefix_count + offset]
        for offset in range(alternative_count)
    ):
        return candidate_end
    return fallback_end


def _selects_complete_old_partial_new_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether a mixed run selects all old rows and a new prefix."""
    deletion_count = 0
    selected_deletion_count = 0
    addition_count = 0
    selected_addition_prefix = 0
    addition_prefix_ended = False
    selected_after_prefix = False

    def matches() -> bool:
        return (
            deletion_count > 0
            and selected_deletion_count == deletion_count
            and 0 < selected_addition_prefix < addition_count
            and not selected_after_prefix
        )

    for line in line_changes.lines:
        if line.kind == "-":
            deletion_count += 1
            if line.id is not None and line.id in selected_ids:
                selected_deletion_count += 1
            continue
        if line.kind == "+":
            addition_count += 1
            is_selected = line.id is not None and line.id in selected_ids
            if is_selected and not addition_prefix_ended:
                selected_addition_prefix += 1
            elif is_selected:
                selected_after_prefix = True
            else:
                addition_prefix_ended = True
            continue
        if matches():
            return True
        deletion_count = 0
        selected_deletion_count = 0
        addition_count = 0
        selected_addition_prefix = 0
        addition_prefix_ended = False
        selected_after_prefix = False
    return matches()


def _selected_run_has_deletion(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether the selected changed block also deletes lines."""
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_has_selection = False
        run_has_deletion = False
        while line_index < len(line_changes.lines) and line_changes.lines[
            line_index
        ].kind in ("+", "-"):
            line = line_changes.lines[line_index]
            if line.id is not None and line.id in selected_ids:
                run_has_selection = True
            if line.kind == "-":
                run_has_deletion = True
            line_index += 1
        if run_has_selection:
            return run_has_deletion
    return False


def _selected_run_has_unselected_deletion(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether the selection's enclosing +/- run also deletes lines.

    An addition selected independently from the rest of a mixed run is still
    one piece of that run's replacement, not a free-floating insertion; its
    enclosing run keeps at least one deletion even though none of the
    deletions are themselves selected.
    """
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_has_selection = False
        run_has_deletion = False
        while (
            line_index < len(line_changes.lines)
            and line_changes.lines[line_index].kind in ("+", "-")
        ):
            line = line_changes.lines[line_index]
            if line.id is not None and line.id in selected_ids:
                run_has_selection = True
            if line.kind == "-":
                run_has_deletion = True
            line_index += 1
        if run_has_selection:
            return run_has_deletion
    return False


def _selected_run_has_unselected_addition(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return whether the selection is a proper subspan of its added side."""
    line_index = 0
    while line_index < len(line_changes.lines):
        if line_changes.lines[line_index].kind not in ("+", "-"):
            line_index += 1
            continue
        run_has_selection = False
        run_has_unselected_addition = False
        while (
            line_index < len(line_changes.lines)
            and line_changes.lines[line_index].kind in ("+", "-")
        ):
            line = line_changes.lines[line_index]
            if line.id is not None and line.id in selected_ids:
                run_has_selection = True
            elif line.kind == "+":
                run_has_unselected_addition = True
            line_index += 1
        if run_has_selection:
            return run_has_unselected_addition
    return False


def _selects_added_side_prefix(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> bool:
    """Return True when the selection starts an added block but does not end it."""
    addition_count = 0
    selected_prefix_count = 0
    addition_prefix_ended = False
    selected_after_prefix = False

    def matches() -> bool:
        return 0 < selected_prefix_count < addition_count and not selected_after_prefix

    for line in line_changes.lines:
        if line.kind == "+":
            addition_count += 1
            is_selected = line.id is not None and line.id in selected_ids
            if is_selected and not addition_prefix_ended:
                selected_prefix_count += 1
            elif is_selected:
                selected_after_prefix = True
            else:
                addition_prefix_ended = True
            continue
        if line.kind == "-":
            continue
        if matches():
            return True
        addition_count = 0
        selected_prefix_count = 0
        addition_prefix_ended = False
        selected_after_prefix = False
    return matches()


def _contiguous_selected_addition_count(
    line_changes: LineLevelChange,
    selected_ids: set[int],
) -> int | None:
    """Return the count when selected rows are one contiguous added span."""
    selected_count = 0
    previous_new_line: int | None = None
    for line in line_changes.lines:
        if line.id is None or line.id not in selected_ids:
            continue
        new_line = line.new_line_number
        if (
            line.kind != "+"
            or new_line is None
            or (previous_new_line is not None and new_line != previous_new_line + 1)
        ):
            return None
        selected_count += 1
        previous_new_line = new_line
    return selected_count or None


def _selected_additions_cover_working_span(
    line_changes: LineLevelChange,
    selected_ids: set[int],
    *,
    replacement_start: int,
    replacement_end: int,
) -> bool:
    """Return whether selected additions exactly cover the working span.

    Deletions may be interleaved with the additions.  This recognizes a
    semantic replacement selected from inside a larger diff block without
    treating unchanged gaps as part of the supplied batch prefix.
    """
    if replacement_start >= replacement_end:
        return False
    expected_new_line = replacement_start + 1
    addition_seen = False
    for line in line_changes.lines:
        if line.id is None or line.id not in selected_ids:
            continue
        if line.kind == "-":
            if addition_seen:
                return False
            continue
        if line.kind != "+" or line.new_line_number != expected_new_line:
            return False
        addition_seen = True
        expected_new_line += 1
    return expected_new_line == replacement_end + 1


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
    """Select rewritten rows whose inverse preserves each live alternative.

    A selected addition with no baseline overlap normally reverts to
    nothing, since it never displaced any old content. When it shares its
    original run with a real deletion, though, it is one wording of an
    already in-progress replacement rather than a free-floating insertion,
    so its rewritten wording stays visible on disk instead of vanishing.
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
        if (
            (line.kind == "+" and not preserve_selected_additions)
            or (line.kind == "-" and not old_line_is_protected)
        ):
            discard_builder.add_line(line_id)
    return discard_builder.finish()


@dataclass(frozen=True)
class _ExpandedReplacementParent:
    parent: ReplacementLineRun
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges


@dataclass(frozen=True)
class _ExpansionCandidate:
    """One selected run that may need its complete semantic old parent."""

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    original_old_count: int
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges


def _expansion_candidates(
    selection: DiscardLineReplacementSelection,
) -> tuple[_ExpansionCandidate, ...]:
    candidates: list[_ExpansionCandidate] = []
    for selection_run in selection.rewritten_selection_runs:
        original_old_lines = selection_run.original_old_lines
        original_new_lines = selection_run.original_new_lines
        rewritten_deletion_ids = selection_run.rewritten_deletion_ids
        if not (
            original_old_lines
            and original_new_lines
            and not selection_run.restore_deletions
            and (
                rewritten_deletion_ids
                or (
                    selection.replacement_alternatives is not None
                    and selection.replacement_alternatives.parent is not None
                )
            )
        ):
            continue
        candidates.append(
            _ExpansionCandidate(
                old_start=original_old_lines.ranges()[0][0],
                old_end=original_old_lines.ranges()[-1][1],
                new_start=original_new_lines.ranges()[0][0],
                new_end=original_new_lines.ranges()[-1][1],
                original_old_count=original_old_lines.count(),
                rewritten_deletion_ids=rewritten_deletion_ids,
                rewritten_addition_ids=selection_run.rewritten_addition_ids,
            )
        )
    return tuple(candidates)


def _selection_may_need_parent_expansion(
    selection: DiscardLineReplacementSelection,
) -> bool:
    return bool(_expansion_candidates(selection))


def _merged_explicit_replacement_parent(
    selection: DiscardLineReplacementSelection,
    expanded_parents: list[_ExpandedReplacementParent],
) -> _ExpandedReplacementParent | None:
    """Return the exact transformed parent for a batch/live payload split."""
    alternatives = selection.replacement_alternatives
    if alternatives is None or alternatives.parent is None:
        return None
    explicit_parent = alternatives.parent.as_line_run()
    prefix = alternatives.saved.span

    old_start = explicit_parent.old_start
    old_end = explicit_parent.old_end
    new_start = explicit_parent.new_start
    new_end = explicit_parent.new_end
    for expanded in expanded_parents:
        old_start = min(old_start, expanded.parent.old_start)
        old_end = max(old_end, expanded.parent.old_end)
        new_start = min(new_start, expanded.parent.new_start)
        new_end = max(new_end, expanded.parent.new_end)
    parent = ReplacementLineRun(old_start, old_end, new_start, new_end)

    deletion_builder = LineRangeBuilder()
    addition_builder = LineRangeBuilder()
    selected_ranges = selection.rewritten_selected_ids.ranges()
    selected_range_index = 0
    for line in selection.rewritten_line_changes.lines:
        line_id = line.id
        if line_id is None:
            continue
        selected_range_index, line_is_selected = _advance_ordered_range_membership(
            selected_ranges,
            selected_range_index,
            line_id,
        )
        if not line_is_selected:
            continue
        if (
            line.kind == "-"
            and line.old_line_number is not None
            and parent.old_start <= line.old_line_number <= parent.old_end
        ):
            deletion_builder.add_line(line_id)
        elif (
            line.kind == "+"
            and line.new_line_number is not None
            and prefix.start.offset < line.new_line_number <= prefix.end.offset
        ):
            addition_builder.add_line(line_id)

    return _ExpandedReplacementParent(
        parent=parent,
        rewritten_deletion_ids=deletion_builder.finish(),
        rewritten_addition_ids=addition_builder.finish(),
    )


def _line_is_delimiter_only(content: bytes) -> bool:
    """Return whether a line has no identifier-like payload."""
    return not any(
        byte == ord("_")
        or ord("0") <= byte <= ord("9")
        or ord("A") <= byte <= ord("Z")
        or ord("a") <= byte <= ord("z")
        or byte >= 0x80
        for byte in content
    )


def _expand_parent_through_relocated_prefix_context(
    parent: ReplacementLineRun,
    *,
    baseline_lines: Sequence[bytes],
    original_working_lines: Sequence[bytes],
    rewritten_prefix_lines: Sequence[bytes],
) -> ReplacementLineRun:
    """Include trailing baseline delimiters relocated past an owned prefix.

    A transformed replacement can own a closing delimiter while structural
    matching associates the corresponding baseline delimiter with a later,
    adjacent block.  Trusting that association leaves the baseline delimiter
    behind in the batch in addition to the owned copy.  Walk only the local
    delimiter tail and expand through an exact prefix duplicate; a contentful
    line ends the inference.
    """
    if parent.old_end >= len(baseline_lines) or not rewritten_prefix_lines:
        return parent

    expanded_old_end = parent.old_end
    expanded_new_end = parent.new_end
    with (
        MatcherWorkspace() as workspace,
        match_lines(baseline_lines, original_working_lines) as alignment,
    ):
        prefix_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            rewritten_prefix_lines,
            normalize_payloads=False,
        )
        for source_line in range(parent.old_end + 1, len(baseline_lines) + 1):
            content = baseline_lines[source_line - 1]
            if not _line_is_delimiter_only(content):
                break
            target_line = alignment.get_target_line_from_source_line(source_line)
            prefix_count = prefix_occurrences.occurrence_count(content)
            if (
                content.strip()
                and prefix_count == 1
                and target_line is not None
                and target_line > parent.new_end
            ):
                expanded_old_end = source_line
                expanded_new_end = max(expanded_new_end, target_line)

    if expanded_old_end == parent.old_end:
        return parent
    return ReplacementLineRun(
        old_start=parent.old_start,
        old_end=expanded_old_end,
        new_start=parent.new_start,
        new_end=expanded_new_end,
    )


def _expand_explicit_parent_relocated_context(
    selection: DiscardLineReplacementSelection,
    expanded_parent: _ExpandedReplacementParent,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer,
) -> _ExpandedReplacementParent:
    """Expand an explicit parent when its owned prefix shadows later context."""
    alternatives = selection.replacement_alternatives
    if alternatives is None:
        return expanded_parent
    prefix_start, prefix_end = alternatives.saved_range
    prefix_lines = LineRangeView(
        selection.rewritten_working_lines,
        prefix_start - 1,
        prefix_end,
    )
    parent = _expand_parent_through_relocated_prefix_context(
        expanded_parent.parent,
        baseline_lines=baseline_lines,
        original_working_lines=original_working_lines,
        rewritten_prefix_lines=prefix_lines,
    )
    if parent == expanded_parent.parent:
        return expanded_parent
    return _ExpandedReplacementParent(
        parent=parent,
        rewritten_deletion_ids=expanded_parent.rewritten_deletion_ids,
        rewritten_addition_ids=expanded_parent.rewritten_addition_ids,
    )


def _expanded_selected_replacement_parents(
    selection: DiscardLineReplacementSelection,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer | None,
) -> tuple[_ExpandedReplacementParent, ...]:
    """Return selected parents whose old side became rewritten context."""
    candidates = _expansion_candidates(selection)
    if not candidates:
        explicit_parent = _merged_explicit_replacement_parent(selection, [])
        return (explicit_parent,) if explicit_parent is not None else ()
    if original_working_lines is None:
        return ()

    parents: list[_ExpandedReplacementParent] = []
    candidate_index = 0
    hunk_index = 0
    semantic_parents = stream_replacement_line_runs_from_lines(
        old_file_lines=baseline_lines,
        new_file_lines=original_working_lines,
    )
    try:
        for parent in semantic_parents:
            while candidate_index < len(candidates):
                candidate = candidates[candidate_index]
                if (
                    candidate.old_end < parent.old_start
                    or candidate.new_end < parent.new_start
                ):
                    candidate_index += 1
                    continue
                break
            if candidate_index >= len(candidates):
                break

            matched: list[_ExpansionCandidate] = []
            scan_index = candidate_index
            while scan_index < len(candidates):
                candidate = candidates[scan_index]
                if (
                    candidate.old_start > parent.old_end
                    or candidate.new_start > parent.new_end
                ):
                    break
                if (
                    candidate.old_start >= parent.old_start
                    and candidate.old_end <= parent.old_end
                    and candidate.new_start >= parent.new_start
                    and candidate.new_end <= parent.new_end
                ):
                    matched.append(candidate)
                    scan_index += 1
                    continue
                break
            if not matched:
                continue
            candidate_index = scan_index

            visible_parent_old_line_count = 0
            while hunk_index < len(selection.line_changes.lines):
                line = selection.line_changes.lines[hunk_index]
                old_line_number = line.old_line_number
                if old_line_number is None:
                    hunk_index += 1
                    continue
                if old_line_number < parent.old_start:
                    hunk_index += 1
                    continue
                if old_line_number > parent.old_end:
                    break
                if line.kind == "-":
                    visible_parent_old_line_count += 1
                hunk_index += 1

            original_old_count = sum(
                candidate.original_old_count for candidate in matched
            )
            rewritten_deletion_ids = LineRanges.from_ranges(
                range_pair
                for candidate in matched
                for range_pair in candidate.rewritten_deletion_ids.ranges()
            )
            if (
                visible_parent_old_line_count == original_old_count
                and len(rewritten_deletion_ids) < parent.old_end - parent.old_start + 1
            ):
                parents.append(
                    _ExpandedReplacementParent(
                        parent=parent,
                        rewritten_deletion_ids=rewritten_deletion_ids,
                        rewritten_addition_ids=LineRanges.from_ranges(
                            range_pair
                            for candidate in matched
                            for range_pair in candidate.rewritten_addition_ids.ranges()
                        ),
                    )
                )
    finally:
        close = getattr(semantic_parents, "close", None)
        if close is not None:
            close()
    explicit_parent = _merged_explicit_replacement_parent(selection, parents)
    if explicit_parent is not None:
        explicit_parent = _expand_explicit_parent_relocated_context(
            selection,
            explicit_parent,
            baseline_lines=baseline_lines,
            original_working_lines=original_working_lines,
        )
        return (explicit_parent,)
    return tuple(parents)


def _add_expanded_replacement_parents(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    expanded_parents: tuple[_ExpandedReplacementParent, ...],
    baseline_lines: LineBuffer,
    source_projection: SourceCoordinateProjection,
    replacement_origin_source_projection: (
        ReplacementOriginSourceProjection[RewrittenWorktreeSpace]
    ),
) -> BatchOwnership:
    """Add full semantic-parent absence claims without dropping other units."""
    deletions = list(ownership.deletions)
    replacement_units = list(ownership.replacement_units)
    hunk_index = 0
    anchor_hunk_index = 0
    hunk_lines = selection.rewritten_line_changes.lines

    def source_line_for(line: LineEntry) -> int | None:
        return source_projection.source_line_for(line)

    def source_bound_origin_for(
        parent: ReplacementLineRun,
    ) -> ReplacementUnitOrigin | None:
        origin = replacement_unit_origin_for_line_run(
            parent,
            old_file_lines=baseline_lines,
        )
        original_span = SnapshotSpan(
            selection.transformed_projection.explicit_edit.source_snapshot,
            LineSpan(
                LineBoundary(parent.new_start - 1),
                LineBoundary(parent.new_end),
            ),
        )
        rewritten_span = (
            selection.transformed_projection.explicit_edit.translate_span(
                original_span
            )
        )
        if rewritten_span is None:
            return None
        source_span = replacement_origin_source_projection.translate_span(
            rewritten_span
        )
        return (
            origin.with_batch_source_span(source_span)
            if source_span is not None
            else None
        )

    for expanded_parent in expanded_parents:
        deletion_first = expanded_parent.rewritten_deletion_ids.first()
        addition_first = expanded_parent.rewritten_addition_ids.first()
        if deletion_first is None and addition_first is None:
            first_id = None
            last_id = None
        else:
            deletion_last = (
                expanded_parent.rewritten_deletion_ids.ranges()[-1][1]
                if deletion_first is not None
                else None
            )
            addition_last = (
                expanded_parent.rewritten_addition_ids.ranges()[-1][1]
                if addition_first is not None
                else None
            )
            first_id = min(
                line_id
                for line_id in (deletion_first, addition_first)
                if line_id is not None
            )
            last_id = max(
                line_id
                for line_id in (deletion_last, addition_last)
                if line_id is not None
            )

        deletion_anchor: int | None = None
        found_deletion = False
        presence_builder = LineRangeBuilder()
        deletion_ranges = expanded_parent.rewritten_deletion_ids.ranges()
        addition_ranges = expanded_parent.rewritten_addition_ids.ranges()
        deletion_range_index = 0
        addition_range_index = 0
        while first_id is not None and hunk_index < len(hunk_lines):
            line = hunk_lines[hunk_index]
            line_id = line.id
            if line_id is None or line_id < first_id:
                hunk_index += 1
                continue
            if last_id is not None and line_id > last_id:
                break
            deletion_range_index, line_is_deletion = _advance_ordered_range_membership(
                deletion_ranges,
                deletion_range_index,
                line_id,
            )
            addition_range_index, line_is_addition = _advance_ordered_range_membership(
                addition_ranges,
                addition_range_index,
                line_id,
            )
            if line_is_deletion:
                if not found_deletion:
                    deletion_anchor = source_line_for(line)
                    found_deletion = True
            source_line = source_line_for(line)
            if line_is_addition and source_line is not None:
                presence_builder.add_line(source_line)
            hunk_index += 1
        parent = expanded_parent.parent
        if not found_deletion:
            while anchor_hunk_index < len(hunk_lines):
                line = hunk_lines[anchor_hunk_index]
                old_line_number = line.old_line_number
                if old_line_number is None or old_line_number < parent.old_start:
                    anchor_hunk_index += 1
                    continue
                if old_line_number > parent.old_end:
                    break
                source_line = source_line_for(line)
                if source_line is not None:
                    deletion_anchor = source_line
                    found_deletion = True
                    break
                anchor_hunk_index += 1
        if not found_deletion and addition_first is not None:
            first_presence = presence_builder.finish().first()
            if first_presence is not None:
                deletion_anchor = max(first_presence - 1, 0)
                found_deletion = True
        if not found_deletion:
            continue
        presence_lines = presence_builder.finish()
        deletions.append(
            AbsenceClaim(
                anchor_line=deletion_anchor,
                content_lines=LineRangeView(
                    baseline_lines,
                    parent.old_start - 1,
                    parent.old_end,
                ),
                baseline_reference=baseline_reference_for_file_line_range(
                    parent.old_start,
                    parent.old_end,
                    baseline_lines,
                ),
            )
        )
        if presence_lines:
            replacement_units.append(
                ReplacementUnit(
                    presence_lines=presence_lines.to_range_strings(),
                    deletion_indices=[len(deletions) - 1],
                    origin=source_bound_origin_for(parent),
                )
            )
    return BatchOwnership(
        presence_claims=ownership.presence_claims,
        deletions=deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(deletions),
        ),
    )
