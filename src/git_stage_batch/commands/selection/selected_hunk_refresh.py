"""Command-layer selected hunk refresh helpers."""

from __future__ import annotations

from ...data.hunk_tracking import (
    RecalculateSelectedHunkResult,
    recalculate_selected_hunk_for_file,
)
from ...output import print_remaining_line_changes_header


def recalculate_selected_hunk_for_command(
    file_path: str,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Refresh selected hunk state and perform command-layer follow-up display."""
    result = recalculate_selected_hunk_for_file(
        file_path,
        auto_advance=auto_advance,
    )
    if result == RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE:
        from ..show import command_show

        command_show()


def refresh_selected_hunk_after_line_action(
    file_path: str,
    *,
    auto_advance: bool | None = None,
) -> None:
    """Print the line-action boundary and refresh selected hunk state."""
    print_remaining_line_changes_header(file_path)
    recalculate_selected_hunk_for_command(
        file_path,
        auto_advance=auto_advance,
    )
