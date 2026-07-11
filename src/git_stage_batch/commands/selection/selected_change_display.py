"""Command-layer selected change display helpers."""

from __future__ import annotations

from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.store import load_line_changes_from_patch_path
from ...data.selected_change.file_changes import (
    load_selected_binary_file,
    load_selected_gitlink_change,
    load_selected_mode_change,
    load_selected_rename_change,
    load_selected_text_deletion_change,
)
from ...output.hunk import print_line_level_changes
from ...output.patch import (
    print_binary_file_change,
    print_gitlink_change,
    print_file_mode_change,
    print_rename_change,
    print_text_file_deletion_change,
)
from ...utils.paths import get_selected_hunk_patch_file_path


def show_selected_change() -> None:
    """Display the currently cached selected change."""
    rename_change = load_selected_rename_change()
    if rename_change is not None:
        print_rename_change(rename_change)
        return

    mode_change = load_selected_mode_change()
    if mode_change is not None:
        print_file_mode_change(mode_change)
        return

    deletion_change = load_selected_text_deletion_change()
    if deletion_change is not None:
        print_text_file_deletion_change(deletion_change)
        return

    gitlink_change = load_selected_gitlink_change()
    if gitlink_change is not None:
        print_gitlink_change(gitlink_change)
        return

    binary_file = load_selected_binary_file()
    if binary_file is not None:
        print_binary_file_change(binary_file)
        return

    patch_path = get_selected_hunk_patch_file_path()
    if patch_path.exists():
        line_changes = load_line_changes_from_state()
        if line_changes is None:
            line_changes = load_line_changes_from_patch_path(patch_path)
        print_line_level_changes(line_changes)
