"""Line-replacement support for include commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...batch.source.annotation import annotate_with_batch_source
from ...batch.selection import require_line_selection_in_view
from ...core.buffer import LineBuffer
from ...core.line_selection import format_line_ids, parse_line_selection
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.file_hunk_display import render_unstaged_file_as_single_hunk
from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.line_state import load_line_changes_from_state
from ...utils.repository_buffers import (
    load_git_object_as_buffer_or_empty,
    load_working_tree_file_as_buffer,
)
from ...data.selected_change.loading import require_selected_hunk
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
    snapshot_selected_change_state,
)
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.index_update import update_index_with_blob_buffer
from ...staging.content_buffers import (
    build_target_index_buffer_with_replaced_lines,
)
from ...utils.paths import (
    get_index_snapshot_file_path,
    get_working_tree_snapshot_file_path,
)
from . import include_line_selection as _include_line_selection
from .consumed_selection_recording import record_consumed_selection
from . import replacement_selection


@dataclass(frozen=True)
class IncludeLineReplacementSelection:
    """Prepared pathless include replacement selection."""

    display_line_changes: object
    replacement_line_changes: object
    line_id_specification: str
    base_buffer: LineBuffer
    source_buffer: LineBuffer


@dataclass(frozen=True)
class IncludeLineReplacementFileSelection:
    """Prepared file-scoped include replacement selection."""

    target_file: str
    line_changes: object
    base_buffer: LineBuffer
    source_buffer: LineBuffer
    preserve_selected_state: bool = False
    saved_selected_state: object | None = None


def apply_include_line_replacement(
    line_changes,
    *,
    line_id_specification: str,
    replacement_text: str | ReplacementPayload,
    hunk_base_lines: Sequence[bytes],
    hunk_source_lines: LineBuffer,
    trim_unchanged_edge_anchors: bool,
) -> None:
    """Stage replacement text for selected lines and record session masking."""
    requested_ids = set(parse_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    effective_ids = replacement_selection.expand_replacement_selection_ids(
        line_changes,
        requested_ids,
    )

    selected_lines = [line for line in line_changes.lines if line.id in effective_ids]
    if not selected_lines:
        exit_with_error(
            _("No matching lines found for selection: {ids}").format(
                ids=line_id_specification
            )
        )

    replacement_payload = coerce_replacement_payload(replacement_text)
    try:
        target_index_buffer = build_target_index_buffer_with_replaced_lines(
            line_changes,
            effective_ids,
            replacement_payload,
            hunk_base_lines,
            base_has_trailing_newline=(
                _include_line_selection.line_sequence_ends_with_lf(hunk_base_lines)
            ),
            trim_unchanged_edge_anchors=trim_unchanged_edge_anchors,
        )
    except ValueError as error:
        exit_with_error(str(error))

    with target_index_buffer:
        update_index_with_blob_buffer(line_changes.path, target_index_buffer)
    record_consumed_selection(
        line_changes.path,
        source_buffer=hunk_source_lines,
        selected_lines=selected_lines,
        replacement_mask={
            "deleted_lines": replacement_payload.as_text().splitlines(),
            "added_lines": [
                line.display_text()
                for line in selected_lines
                if line.kind == "+"
            ],
        },
    )


def prepare_pathless_include_line_replacement(
    line_id_specification: str,
) -> IncludeLineReplacementSelection:
    """Prepare replacement context for include --line --as."""
    require_selected_hunk()
    line_changes = load_line_changes_from_state()
    replacement_line_changes = line_changes
    replacement_line_id_specification = line_id_specification
    replacement_base_buffer = None
    replacement_source_buffer = None
    selected_change_kind = read_selected_change_kind()
    requested_ids = set(parse_line_selection(line_id_specification))
    if selected_change_kind == SelectedChangeKind.FILE:
        require_line_selection_in_view(
            line_changes,
            requested_ids,
            line_id_specification=line_id_specification,
        )
        translated_replacement = translate_file_view_replacement_to_unstaged_diff(
            line_changes,
            requested_ids,
        )
        if translated_replacement is not None:
            replacement_line_changes, replacement_ids = translated_replacement
            replacement_line_id_specification = format_line_ids(sorted(replacement_ids))
            replacement_base_buffer = load_git_object_as_buffer_or_empty(
                f":{line_changes.path}"
            )
            replacement_source_buffer = load_working_tree_file_as_buffer(
                line_changes.path
            )
    if replacement_base_buffer is None:
        replacement_base_buffer = LineBuffer.from_path(get_index_snapshot_file_path())
    if replacement_source_buffer is None:
        replacement_source_buffer = LineBuffer.from_path(
            get_working_tree_snapshot_file_path()
        )

    return IncludeLineReplacementSelection(
        display_line_changes=line_changes,
        replacement_line_changes=replacement_line_changes,
        line_id_specification=replacement_line_id_specification,
        base_buffer=replacement_base_buffer,
        source_buffer=replacement_source_buffer,
    )


def prepare_file_include_line_replacement(
    file: str,
    selected_state_stack,
) -> IncludeLineReplacementFileSelection:
    """Prepare replacement context for include --line --as --file."""
    preserve_selected_state = False
    saved_selected_state = None

    if file == "":
        target_file = get_selected_change_file_path()
        if target_file is None:
            exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
    else:
        target_file = file

    selected_file_view_targets_file = _include_line_selection.selected_file_view_targets(
        target_file
    )
    reuse_selected_file_view = _include_line_selection.selected_file_view_is_fresh_for(
        target_file
    )
    if reuse_selected_file_view:
        cached_lines = load_line_changes_from_state()
        if cached_lines is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))
    else:
        if file != "" and not selected_file_view_targets_file:
            preserve_selected_state = True
            saved_selected_state = selected_state_stack.enter_context(
                snapshot_selected_change_state()
            )

        cached_lines = cache_unstaged_file_as_single_hunk(target_file)
        if cached_lines is None:
            exit_with_error(_("No changes in file '{file}'.").format(file=target_file))

    return IncludeLineReplacementFileSelection(
        target_file=target_file,
        line_changes=annotate_with_batch_source(target_file, cached_lines),
        base_buffer=load_git_object_as_buffer_or_empty(f":{target_file}"),
        source_buffer=load_working_tree_file_as_buffer(target_file),
        preserve_selected_state=preserve_selected_state,
        saved_selected_state=saved_selected_state,
    )


def _line_identity_for_live_replacement(line) -> tuple[str, int | None, bytes, bool]:
    """Return a stable identity for a changed line in a live file view."""
    return (
        line.kind,
        line.source_line,
        line.text_bytes,
        line.has_trailing_newline,
    )


def translate_file_view_replacement_to_unstaged_diff(
    line_changes,
    requested_ids: set[int],
):
    """Map file-vs-HEAD review IDs to the current unstaged diff, if possible."""
    effective_ids = replacement_selection.expand_replacement_selection_ids(
        line_changes,
        requested_ids,
    )
    selected_lines = [line for line in line_changes.lines if line.id in effective_ids]
    if not selected_lines:
        return None

    unstaged_line_changes = render_unstaged_file_as_single_hunk(line_changes.path)
    if unstaged_line_changes is None:
        return None

    annotated_selected_changes = (
        _include_line_selection.annotate_line_changes_with_working_tree_source(
            line_changes
        )
    )
    annotated_unstaged_changes = (
        _include_line_selection.annotate_line_changes_with_working_tree_source(
            unstaged_line_changes
        )
    )
    if annotated_selected_changes is None or annotated_unstaged_changes is None:
        return None

    unstaged_ids_by_identity: dict[tuple[str, int | None, bytes, bool], list[int]] = {}
    for line in annotated_unstaged_changes.lines:
        if line.id is None:
            continue
        unstaged_ids_by_identity.setdefault(
            _line_identity_for_live_replacement(line),
            [],
        ).append(line.id)

    translated_ids: list[int] = []
    for line in annotated_selected_changes.lines:
        if line.id not in effective_ids:
            continue
        candidate_ids = unstaged_ids_by_identity.get(
            _line_identity_for_live_replacement(line),
        )
        if not candidate_ids:
            return None
        translated_ids.append(candidate_ids.pop(0))

    if not translated_ids:
        return None

    return annotated_unstaged_changes, set(translated_ids)
