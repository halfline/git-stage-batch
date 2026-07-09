"""File-scope target path resolution."""

from __future__ import annotations

from ...data.selected_change.paths import get_selected_change_file_path
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
