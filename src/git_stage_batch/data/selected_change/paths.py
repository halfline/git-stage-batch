"""Selected-change path resolution."""

from __future__ import annotations

from ...core.models import (
    BinaryFileChange,
    FileModeChange,
    GitlinkChange,
    LineLevelChange,
    RenameChange,
    TextFileDeletionChange,
)
from ...utils.paths import get_selected_hunk_patch_file_path
from . import file_changes as _selected_file_changes
from .store import load_line_changes_from_patch_path


SelectedChange = (
    BinaryFileChange
    | FileModeChange
    | GitlinkChange
    | LineLevelChange
    | RenameChange
    | TextFileDeletionChange
)


def file_path_for_selected_change(change: SelectedChange) -> str:
    """Return the repository path that identifies a selected change."""
    if isinstance(change, LineLevelChange):
        return change.path
    return change.path()


def worktree_paths_for_selected_change(change: SelectedChange) -> list[str]:
    """Return worktree paths that can be affected by a selected-change action."""
    if isinstance(change, RenameChange):
        return [change.old_path, change.new_path]
    return [file_path_for_selected_change(change)]


def get_selected_change_file_path() -> str | None:
    """Return the file path for the currently cached selected change."""
    rename_change = _selected_file_changes.load_selected_rename_change()
    if rename_change is not None:
        return rename_change.path()

    mode_change = _selected_file_changes.load_selected_mode_change()
    if mode_change is not None:
        return mode_change.path()

    deletion_change = _selected_file_changes.load_selected_text_deletion_change()
    if deletion_change is not None:
        return deletion_change.path()

    gitlink_change = _selected_file_changes.load_selected_gitlink_change()
    if gitlink_change is not None:
        return gitlink_change.path()

    binary_file = _selected_file_changes.load_selected_binary_file()
    if binary_file is not None:
        return binary_file.path()

    patch_path = get_selected_hunk_patch_file_path()
    if not patch_path.exists():
        return None

    line_changes = load_line_changes_from_patch_path(patch_path)
    return line_changes.path
