"""Batch line-selection support for command implementations."""

from __future__ import annotations

from dataclasses import dataclass

from ...batch.selection import require_line_selection_in_view
from ...core.line_selection import parse_line_selection


@dataclass(frozen=True)
class BatchLineSelection:
    """Line IDs and matching changed lines selected for a batch action."""

    requested_ids: set[int]
    selected_lines: list


def select_lines_for_batch_action(
    line_changes,
    line_id_specification: str,
) -> BatchLineSelection:
    """Validate line IDs against a view and return matching changed lines."""
    requested_ids = set(parse_line_selection(line_id_specification))
    require_line_selection_in_view(
        line_changes,
        requested_ids,
        line_id_specification=line_id_specification,
    )
    return BatchLineSelection(
        requested_ids=requested_ids,
        selected_lines=[
            line for line in line_changes.lines if line.id in requested_ids
        ],
    )
