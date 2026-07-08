"""Selected-change path resolution."""

from __future__ import annotations

from ...utils.paths import get_selected_hunk_patch_file_path
from . import file_changes as _selected_file_changes
from .store import load_line_changes_from_patch_path


def get_selected_change_file_path() -> str | None:
    """Return the file path for the currently cached selected change."""
    rename_change = _selected_file_changes.load_selected_rename_change()
    if rename_change is not None:
        return rename_change.path()

    deletion_change = _selected_file_changes.load_selected_text_deletion_change()
    if deletion_change is not None:
        return deletion_change.path()

    gitlink_change = _selected_file_changes.load_selected_gitlink_change()
    if gitlink_change is not None:
        return gitlink_change.path()

    binary_file = _selected_file_changes.load_selected_binary_file()
    if binary_file is not None:
        return (
            binary_file.new_path
            if binary_file.new_path != "/dev/null" else
            binary_file.old_path
        )

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    line_changes = load_line_changes_from_patch_path(patch_path)
    return line_changes.path
