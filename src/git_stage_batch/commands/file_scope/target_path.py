"""File-scope target path resolution."""

from __future__ import annotations

from ...core.diff_parser import acquire_unified_diff
from ...core.models import RenameChange
from ...data.live_diff import stream_live_git_diff
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


def checkpoint_paths_for_live_file(target_file: str) -> list[str]:
    """Return every path a live whole-file action may mutate."""
    with acquire_unified_diff(
        stream_live_git_diff(
            full_index=True,
            ignore_submodules="none",
            submodule_format="short",
        )
    ) as patches:
        for patch in patches:
            if isinstance(patch, RenameChange) and target_file in (
                patch.old_path,
                patch.new_path,
            ):
                return [patch.old_path, patch.new_path]
    return [target_file]
