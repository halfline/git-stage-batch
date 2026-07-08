"""File review display rendering for interactive mode."""

from __future__ import annotations

import sys

from ...data.progress import get_hunk_counts
from ...exceptions import CommandError
from ..display import print_status_bar
from ..flow import FlowState, LocationRole


def render_file_review(
    flow_state: FlowState,
    *,
    file_path: str,
    page_spec: str | None,
) -> bool:
    """Render the reviewed file for the current interactive source."""
    print()
    print_status_bar(get_hunk_counts(), flow_state)
    print()

    try:
        if flow_state.source.role is LocationRole.BATCH:
            from ...commands.show_from import command_show_from_batch

            command_show_from_batch(
                flow_state.source.batch_name,
                file=file_path,
                page=page_spec,
                selectable=True,
            )
        else:
            from ...commands.show import command_show

            command_show(
                file=file_path,
                page=page_spec,
                selectable=True,
            )
    except CommandError as e:
        print(e.message, file=sys.stderr)
        return False
    return True
