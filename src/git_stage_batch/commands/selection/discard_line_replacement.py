"""Line-replacement support for discard commands."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import os
from pathlib import Path

from ...batch.source.annotation import annotate_with_batch_source_working_lines
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
from ...batch.line_matching.line_range_view import LineRangeView
from ...batch.ownership.line_entries import (
    baseline_reference_for_file_line_range,
    replacement_unit_origin_for_line_run,
)
from ...batch.ownership.replacement_units import ReplacementUnit
from ...batch.ownership.replacement_units import normalize_replacement_units
from ...batch.ownership.claims import LineRangeBuilder
from ...batch.merge.baseline_reference_translation import (
    translate_ownership_baseline_references,
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
from ...batch.source.line_coordinates import translate_display_source_coordinates
from ...batch.text_file_storage import add_file_to_batch
from ...batch.state.batch_names import batch_exists
from ...core.buffer import LineBuffer, buffer_ends_with_lf
from ...core.line_selection import LineRanges
from ...core.mapped_storage import MappedRecordVector
from ...core.models import LineLevelChange
from ...core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
    replacement_line_bodies,
)
from ...batch.ownership.model import BatchOwnership
from ...batch.source.cache import (
    load_session_batch_sources,
    save_session_batch_sources,
)
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
    file_path: str
    working_file_path: Path
    rewritten_line_changes: LineLevelChange
    rewritten_selection_runs: tuple[_RewrittenSelectionRun, ...]
    rewritten_selected_ids: LineRanges
    rewritten_worktree_discard_ids: LineRanges
    rewritten_working_lines: LineBuffer
    explicit_replacement_parent: ReplacementLineRun | None = None
    explicit_rewritten_prefix_start: int | None = None
    explicit_rewritten_prefix_end: int | None = None


@dataclass(frozen=True)
class _RewrittenSelectionRun:
    """One original changed run projected into the rewritten diff."""

    original_old_lines: LineRanges
    original_new_lines: LineRanges
    rewritten_deletion_ids: LineRanges
    rewritten_addition_ids: LineRanges
    restore_deletions: bool


@contextmanager
def prepare_discard_line_replacement_selection(
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    *,
    no_edge_overlap: bool = False,
) -> Iterator[DiscardLineReplacementSelection]:
    """Prepare rewritten line selection state for discard-to-batch."""
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        exit_with_error(_("No selected hunk. Run 'start' first."))
    requested_ids = set(parse_command_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    effective_ids = replacement_selection.expand_replacement_selection_ids(
        line_changes,
        requested_ids,
        preserve_partial_addition_prefix=True,
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

    replacement_payload = coerce_replacement_payload(replacement_text)
    replacement_owned_prefix_count: int | None = None
    selects_partial_new_prefix = _selects_complete_old_partial_new_prefix(
        line_changes,
        effective_ids,
    )
    try:
        with load_working_tree_file_as_buffer(line_changes.path) as working_lines:
            original_working_line_count = len(working_lines)
            replacement_start, replacement_end = (
                replacement_working_tree_span_indices(
                    line_changes,
                    effective_ids,
                    original_working_line_count,
                )
            )
            baseline_start, baseline_end = replacement_baseline_span_indices(
                line_changes,
                effective_ids,
                original_working_line_count,
            )
            if (
                selects_partial_new_prefix
                and replacement_start < replacement_end
            ):
                with replacement_line_bodies(replacement_payload) as payload_lines:
                    selected_working_line_count = replacement_end - replacement_start
                    if (
                        len(payload_lines) > selected_working_line_count
                        and all(
                            payload_lines[index]
                            == _line_body(
                                working_lines[replacement_start + index]
                            )
                            for index in range(selected_working_line_count)
                        )
                    ):
                        replacement_owned_prefix_count = selected_working_line_count
            rewritten_working_buffer = (
                build_target_working_tree_buffer_with_replaced_lines(
                    line_changes,
                    effective_ids,
                    replacement_payload,
                    working_lines,
                    working_has_trailing_newline=buffer_ends_with_lf(working_lines),
                    trim_unchanged_edge_anchors=(
                        not no_edge_overlap
                        and replacement_owned_prefix_count is None
                    ),
                )
            )
    except ValueError as error:
        exit_with_error(str(error))

    with rewritten_working_buffer as rewritten_working_lines:
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
        rewritten_cached_lines = build_file_hunk_from_buffer(
            line_changes.path,
            rewritten_working_lines,
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
        rewritten_worktree_discard_ids = _rewritten_worktree_discard_ids(
            rewritten_selection_runs,
            rewritten_span_ids,
            rewritten_line_changes,
        )
        explicit_replacement_parent = None
        if (
            replacement_owned_prefix_count is not None
            and baseline_start < baseline_end
        ):
            explicit_replacement_parent = ReplacementLineRun(
                old_start=baseline_start + 1,
                old_end=baseline_end,
                new_start=replacement_start + 1,
                new_end=replacement_end,
            )
        yield DiscardLineReplacementSelection(
            line_changes=line_changes,
            file_path=line_changes.path,
            working_file_path=working_file_path,
            rewritten_line_changes=rewritten_line_changes,
            rewritten_selection_runs=rewritten_selection_runs,
            rewritten_selected_ids=rewritten_selected_ids,
            rewritten_worktree_discard_ids=rewritten_worktree_discard_ids,
            rewritten_working_lines=rewritten_working_lines,
            explicit_replacement_parent=explicit_replacement_parent,
            explicit_rewritten_prefix_start=(
                replacement_new_start
                if explicit_replacement_parent is not None
                else None
            ),
            explicit_rewritten_prefix_end=(
                owned_replacement_new_end
                if explicit_replacement_parent is not None
                else None
            ),
        )


def build_discard_line_replacement_target_buffer(
    selection: DiscardLineReplacementSelection,
) -> LineBuffer:
    """Return the worktree buffer after removing rewritten replacement lines."""
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
    if not batch_exists(batch_name):
        create_batch(batch_name, "Auto-created")

    metadata = read_batch_metadata(batch_name)
    file_metadata = metadata.get("files", {}).get(selection.file_path)

    with ExitStack() as ownership_stack:
        original_working_lines = (
            ownership_stack.enter_context(
                load_working_tree_file_as_buffer(selection.file_path)
            )
            if _selection_may_need_parent_expansion(selection)
            else None
        )
        batch_source_commit: str | None
        try:
            if file_metadata is None:
                batch_source_commit = create_batch_source_commit(
                    selection.file_path,
                    file_buffer_override=selection.rewritten_working_lines,
                )
                _record_session_batch_source(
                    selection.file_path,
                    batch_source_commit,
                )
                reference_source_lines = ownership_stack.enter_context(
                    read_git_object_buffer_or_empty(
                        f"HEAD:{selection.file_path}"
                    )
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
                with _temporarily_refresh_rewritten_source_lines(
                    selection,
                    map_working_line=lambda line_number: line_number,
                ):
                    ownership = _translate_rewritten_selection_ownership(
                        selection,
                        baseline_lines=reference_source_lines,
                        original_working_lines=original_working_lines,
                        rewritten_lines=selection.rewritten_working_lines,
                    )
                translate_ownership_baseline_references(
                    ownership,
                    reference_source_lines,
                    reference_target_lines,
                    replacement_origin_source_lines=reference_source_lines,
                )
            else:
                ownership, batch_source_commit = _merge_replacement_with_batch(
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
        add_file_to_batch(
            batch_name,
            selection.file_path,
            ownership,
            detect_file_mode(selection.file_path),
            batch_source_commit=batch_source_commit,
        )


def _merge_replacement_with_batch(
    selection: DiscardLineReplacementSelection,
    *,
    file_metadata: BatchFileMetadataDict,
    batch_baseline_commit: str | None,
    original_working_lines: LineBuffer | None,
    ownership_stack: ExitStack,
) -> tuple[BatchOwnership, str]:
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
    with (
        old_source_buffer as old_source_lines,
        advance_source_lines_preserving_existing_presence(
            old_lines=old_source_lines,
            working_lines=selection.rewritten_working_lines,
            ownership=existing_ownership,
        ) as source_with_provenance,
    ):
        remapped_existing_ownership = remap_batch_ownership_with_lineage(
            ownership=existing_ownership,
            lineage=source_with_provenance.lineage,
        )
        with _temporarily_refresh_rewritten_source_lines(
            selection,
            map_working_line=source_with_provenance.lineage.translate_working_line,
            map_existing_source_line=(
                source_with_provenance.lineage.translate_source_line
            ),
        ):
            new_ownership = _translate_rewritten_selection_ownership(
                selection,
                baseline_lines=reference_source_lines,
                original_working_lines=original_working_lines,
                rewritten_lines=selection.rewritten_working_lines,
            )
        translate_ownership_baseline_references(
            new_ownership,
            reference_source_lines,
            reference_target_lines,
            replacement_origin_source_lines=reference_source_lines,
        )
        batch_source_commit = create_batch_source_commit(
            selection.file_path,
            file_buffer_override=source_with_provenance.source_buffer,
        )
        _record_session_batch_source(selection.file_path, batch_source_commit)
        return (
            merge_batch_ownership(remapped_existing_ownership, new_ownership),
            batch_source_commit,
        )


def _record_session_batch_source(file_path: str, batch_source_commit: str) -> None:
    batch_sources = load_session_batch_sources()
    batch_sources[file_path] = batch_source_commit
    save_session_batch_sources(batch_sources)


@contextmanager
def _temporarily_refresh_rewritten_source_lines(
    selection: DiscardLineReplacementSelection,
    *,
    map_working_line: Callable[[int], int | None],
    map_existing_source_line: Callable[[int], int | None] | None = None,
) -> Iterator[None]:
    """Refresh selected source coordinates with storage-backed restoration."""
    coordinate_lines = selection.rewritten_line_changes.lines
    selected_ranges = selection.rewritten_selected_ids.ranges()
    selected_range_index = 0
    with MappedRecordVector(len(coordinate_lines), "QQ") as original_coordinates:
        try:
            for index, (line, source_line) in enumerate(
                translate_display_source_coordinates(
                    coordinate_lines,
                    map_working_line,
                    map_existing_source_line=map_existing_source_line,
                )
            ):
                if line.id is None:
                    continue
                selected_range_index, line_is_selected = (
                    _advance_ordered_range_membership(
                        selected_ranges,
                        selected_range_index,
                        line.id,
                    )
                )
                if not line_is_selected:
                    continue
                original_coordinates.append((
                    index,
                    0 if line.source_line is None else line.source_line + 1,
                ))
                line.source_line = source_line
            yield
        finally:
            for index, encoded_source_line in original_coordinates:
                coordinate_lines[index].source_line = (
                    encoded_source_line - 1
                    if encoded_source_line > 0
                    else None
                )


def _translate_rewritten_selection_ownership(
    selection: DiscardLineReplacementSelection,
    *,
    baseline_lines: LineBuffer,
    original_working_lines: LineBuffer | None,
    rewritten_lines: LineBuffer,
) -> BatchOwnership:
    """Translate the selected rewritten rows with full-hunk provenance."""
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
    selected_ids = selection.rewritten_selected_ids.difference(
        expanded_deletion_ids
    )
    replacement_runs = stream_replacement_line_runs_from_lines(
        old_file_lines=baseline_lines,
        new_file_lines=rewritten_lines,
    )
    ownership = translate_hunk_selection_to_batch_ownership(
        selection.rewritten_line_changes.lines,
        selected_ids,
        replacement_line_runs=replacement_runs,
        replacement_origin_source_lines=baseline_lines,
        replacement_runs_are_origin_runs=True,
        baseline_lines=baseline_lines,
    )
    return _add_expanded_replacement_parents(
        ownership,
        selection=selection,
        expanded_parents=expanded_parents,
        baseline_lines=baseline_lines,
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
        line.id is not None and line.id in selected_ids
        for line in line_changes.lines
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


def _line_body(line: bytes) -> bytes:
    """Return one line without its source line ending."""
    if line.endswith(b"\r\n"):
        return line[:-2]
    if line.endswith(b"\n"):
        return line[:-1]
    return line


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
        if line.kind != " " and (
            old_coordinate_selected or new_coordinate_selected
        ):
            if coordinate_envelope_start is None:
                coordinate_envelope_start = index
            coordinate_envelope_end = index

    coordinate_addition_builder = LineRangeBuilder()
    if (
        coordinate_envelope_start is not None
        and coordinate_envelope_end is not None
    ):
        for index in range(
            coordinate_envelope_start,
            coordinate_envelope_end + 1,
        ):
            line = rewritten_line_changes.lines[index]
            if (
                line.kind == "+"
                and line.id is not None
                and line.new_line_number is not None
                and replacement_new_start
                <= line.new_line_number
                <= replacement_new_end
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
            and replacement_new_start
            <= line.new_line_number
            <= replacement_new_end
            and (
                (
                    owned_prefix_remaining is not None
                    and owned_prefix_remaining > 0
                )
                or addition_is_in_envelope
                or (
                    not addition_is_mapped
                    and remaining_additions > 0
                )
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
            projections.append(_SelectionRunProjection(
                original_old_lines=original_old_builder.finish(),
                original_new_lines=original_new_builder.finish(),
                addition_anchor=selected_addition_anchor,
                addition_limit=(
                    selected_addition_count
                    if selected_addition_count > 0
                    else None
                ),
                rewritten_old_lines=LineRangeBuilder(),
                rewritten_deletion_ids=LineRangeBuilder(),
                rewritten_addition_ids=LineRangeBuilder(),
            ))
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
        if not (
            replacement_new_start <= line.new_line_number <= replacement_new_end
        ):
            continue
        while addition_projection_index < len(projections):
            projection = projections[addition_projection_index]
            window = projection.addition_window()
            if window is None or window[1] < old_anchor:
                addition_projection_index += 1
                continue
            if (
                window[0] <= old_anchor <= window[1]
                and (
                    projection.addition_limit is None
                    or projection.rewritten_addition_count
                    < projection.addition_limit
                )
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
                projection.rewritten_old_lines.finish()
                == projection.original_old_lines
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
) -> LineRanges:
    """Select rewritten rows whose inverse preserves each live alternative."""
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
        selected_range_index, line_is_selected = (
            _advance_ordered_range_membership(
                all_selected_ranges,
                selected_range_index,
                line_id,
            )
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
        if line.kind == "+" or (
            line.kind == "-" and not old_line_is_protected
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
                or selection.explicit_replacement_parent is not None
            )
        ):
            continue
        candidates.append(_ExpansionCandidate(
            old_start=original_old_lines.ranges()[0][0],
            old_end=original_old_lines.ranges()[-1][1],
            new_start=original_new_lines.ranges()[0][0],
            new_end=original_new_lines.ranges()[-1][1],
            original_old_count=original_old_lines.count(),
            rewritten_deletion_ids=rewritten_deletion_ids,
            rewritten_addition_ids=selection_run.rewritten_addition_ids,
        ))
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
    explicit_parent = selection.explicit_replacement_parent
    if explicit_parent is None:
        return None
    prefix_start = selection.explicit_rewritten_prefix_start
    prefix_end = selection.explicit_rewritten_prefix_end
    if prefix_start is None or prefix_end is None:
        raise ValueError("explicit replacement prefix has no rewritten range")

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
        selected_range_index, line_is_selected = (
            _advance_ordered_range_membership(
                selected_ranges,
                selected_range_index,
                line_id,
            )
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
            and prefix_start <= line.new_line_number <= prefix_end
        ):
            addition_builder.add_line(line_id)

    return _ExpandedReplacementParent(
        parent=parent,
        rewritten_deletion_ids=deletion_builder.finish(),
        rewritten_addition_ids=addition_builder.finish(),
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
                and len(rewritten_deletion_ids)
                < parent.old_end - parent.old_start + 1
            ):
                parents.append(_ExpandedReplacementParent(
                    parent=parent,
                    rewritten_deletion_ids=rewritten_deletion_ids,
                    rewritten_addition_ids=LineRanges.from_ranges(
                        range_pair
                        for candidate in matched
                        for range_pair in candidate.rewritten_addition_ids.ranges()
                    ),
                ))
    finally:
        close = getattr(semantic_parents, "close", None)
        if close is not None:
            close()
    explicit_parent = _merged_explicit_replacement_parent(selection, parents)
    if explicit_parent is not None:
        return (explicit_parent,)
    return tuple(parents)


def _add_expanded_replacement_parents(
    ownership: BatchOwnership,
    *,
    selection: DiscardLineReplacementSelection,
    expanded_parents: tuple[_ExpandedReplacementParent, ...],
    baseline_lines: LineBuffer,
) -> BatchOwnership:
    """Add full semantic-parent absence claims without dropping other units."""
    deletions = list(ownership.deletions)
    replacement_units = list(ownership.replacement_units)
    hunk_index = 0
    anchor_hunk_index = 0
    hunk_lines = selection.rewritten_line_changes.lines
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
            deletion_range_index, line_is_deletion = (
                _advance_ordered_range_membership(
                    deletion_ranges,
                    deletion_range_index,
                    line_id,
                )
            )
            addition_range_index, line_is_addition = (
                _advance_ordered_range_membership(
                    addition_ranges,
                    addition_range_index,
                    line_id,
                )
            )
            if line_is_deletion:
                if not found_deletion:
                    deletion_anchor = line.source_line
                    found_deletion = True
            if (
                line_is_addition and line.source_line is not None
            ):
                presence_builder.add_line(line.source_line)
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
                if line.source_line is not None:
                    deletion_anchor = line.source_line
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
        deletions.append(AbsenceClaim(
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
        ))
        if presence_lines:
            replacement_units.append(ReplacementUnit(
                presence_lines=presence_lines.to_range_strings(),
                deletion_indices=[len(deletions) - 1],
                origin=replacement_unit_origin_for_line_run(
                    parent,
                    old_file_lines=baseline_lines,
                ),
            ))
    return BatchOwnership(
        presence_claims=ownership.presence_claims,
        deletions=deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(deletions),
        ),
    )
