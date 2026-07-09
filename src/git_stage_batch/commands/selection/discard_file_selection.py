"""File-scoped selection loading for discard commands."""

from __future__ import annotations

from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.file_tracking import auto_add_untracked_files
from ...data.line_state import load_line_changes_from_state
from ...data.selected_change.paths import get_selected_change_file_path
from ...data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ...exceptions import exit_with_error
from ...i18n import _


def load_explicit_file_selection(file_path: str):
    """Return the active file-scoped view for an explicit discard target."""
    auto_add_untracked_files([file_path])
    reuse_selected_file_view = (
        read_selected_change_kind() == SelectedChangeKind.FILE
        and get_selected_change_file_path() == file_path
    )
    if reuse_selected_file_view:
        line_changes = load_line_changes_from_state()
    else:
        line_changes = cache_unstaged_file_as_single_hunk(file_path)

    if line_changes is None:
        exit_with_error(_("No changes in file '{file}'.").format(file=file_path))
    return line_changes
