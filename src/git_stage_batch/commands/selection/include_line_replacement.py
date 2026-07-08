"""Line-replacement support for include commands."""

from __future__ import annotations

from collections.abc import Sequence

from ...batch.selection import require_line_selection_in_view
from ...core.buffer import LineBuffer
from ...core.line_selection import parse_line_selection
from ...core.replacement import ReplacementPayload, coerce_replacement_payload
from ...data.consumed_selections import record_consumed_selection
from ...exceptions import exit_with_error
from ...i18n import _
from ...staging.operations import (
    build_target_index_buffer_with_replaced_lines,
    update_index_with_blob_buffer,
)
from . import include_line_selection as _include_line_selection
from . import replacement_selection


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
