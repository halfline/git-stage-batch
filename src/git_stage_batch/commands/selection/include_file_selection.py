"""File-scoped selection loading for include commands."""

from __future__ import annotations

from ...data.file_change_display import render_gitlink_change
from ...data.selected_change.file_hunk_cache import cache_unstaged_file_as_single_hunk
from ...data.line_state import load_line_changes_from_state
from ...exceptions import exit_with_error
from ...i18n import _
from .include_line_selection import selected_file_view_targets


def load_explicit_file_selection(file_path: str):
    """Return the active file-scoped view for an explicit include target."""
    if render_gitlink_change(file_path) is not None:
        exit_with_error(
            _("Cannot use --lines with submodule pointers. Include the whole pointer instead.")
        )

    if selected_file_view_targets(file_path):
        line_changes = load_line_changes_from_state()
    else:
        line_changes = cache_unstaged_file_as_single_hunk(file_path)

    if line_changes is None:
        exit_with_error(_("No changes in file '{file}'.").format(file=file_path))
    return line_changes
