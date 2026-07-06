"""Command-layer selected hunk refresh helpers."""

from __future__ import annotations

from ...data.hunk_tracking import (
    RecalculateSelectedHunkResult,
    recalculate_selected_hunk_for_file,
)


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
