"""Show command implementation."""

from __future__ import annotations

from ..batch.selection import parse_command_line_selection_ranges
from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.paths import (
    ensure_state_directory_exists,
)
from .file_scope import file_display_action as _file_display_action
from .file_scope import file_list_action as _file_list_action
from .selection.next_change_display import show_next_unprocessed_change


def command_show_file_list(files: list[str], *, selectable: bool = True) -> None:
    """Show a navigational file list for multiple live file reviews."""
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    _file_list_action.show_live_file_list(files, selectable=selectable)


def command_show(
    file: str | None = None,
    *,
    line_ids: str | None = None,
    page: str | None = None,
    porcelain: bool = False,
    selectable: bool = True,
) -> None:
    """Show the first unprocessed hunk or entire file.

    Args:
        file: Optional file path for file-scoped display.
              If empty string, uses selected hunk's file.
              If None, shows selected hunk (normal behavior).
        page: Optional file-review page selection.
        porcelain: If True, produce no output and exit with code 0 if hunk found, 1 if none
        selectable: If True, cache the file and show selectable gutter IDs.
                    If False, only preview the file and hide gutter IDs.
        line_ids: Row IDs to include when showing a file.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    selected_ids = (
        parse_command_line_selection_ranges(line_ids) if line_ids is not None else None
    )
    if selected_ids is not None and file is None:
        file = ""

    # File-scoped operation
    if file is not None:
        _file_display_action.show_live_file_display(
            file,
            selected_ids=selected_ids,
            page=page,
            porcelain=porcelain,
            selectable=selectable,
        )
        return

    show_next_unprocessed_change(porcelain=porcelain, selectable=selectable)
