"""Current interactive change loading and display."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import LineLevelChange
from ..data.selected_change.batch_file_cache import cache_batch_as_single_hunk
from ..data.line_state import load_line_changes_from_state
from ..data.progress import get_hunk_counts
from ..output.hunk import print_line_level_changes
from .display import print_status_bar
from .flow import FlowState, LocationRole


@dataclass(frozen=True)
class CurrentChange:
    """Loaded change currently displayed in interactive mode."""

    line_changes: LineLevelChange
    gutter_to_selection_id: dict[int, int] | None = None


def load_current_change(flow_state: FlowState) -> CurrentChange | None:
    """Load the change currently selected by the interactive source."""
    if flow_state.source.role is LocationRole.BATCH:
        rendered = cache_batch_as_single_hunk(flow_state.source.batch_name)
        if rendered is None:
            return None
        return CurrentChange(
            line_changes=rendered.line_changes,
            gutter_to_selection_id=rendered.gutter_to_selection_id,
        )

    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return None
    return CurrentChange(line_changes=line_changes)


def display_current_change(current_change: CurrentChange, flow_state: FlowState) -> None:
    """Display the loaded interactive change with current progress."""
    print()
    print_status_bar(get_hunk_counts(), flow_state)
    print()
    print_line_level_changes(
        current_change.line_changes,
        gutter_to_selection_id=current_change.gutter_to_selection_id,
    )
