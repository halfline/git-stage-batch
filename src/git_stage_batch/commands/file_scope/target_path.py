"""File-scope target path resolution."""

from __future__ import annotations

from ...data.selected_change.paths import (
    SelectedChange,
    get_selected_change_file_path,
    worktree_paths_for_selected_change,
)
from ...exceptions import exit_with_error
from ...i18n import _


def require_file_scope_target_path(file: str) -> str:
    """Return the concrete file path for a required file-scope argument."""
    if file != "":
        return file

    target_file = get_selected_change_file_path()
    if target_file is None:
        exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
    return target_file


def checkpoint_paths_for_file_scope(
    file: str | None,
    selected_change: SelectedChange | None,
) -> list[str]:
    """Return concrete paths read by a selected or explicit file operation."""
    if file not in (None, ""):
        return [file]
    if selected_change is not None:
        return worktree_paths_for_selected_change(selected_change)
    target_file = get_selected_change_file_path()
    return [target_file] if target_file is not None else []
