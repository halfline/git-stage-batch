"""Iteration-pass helpers for session commands."""

from __future__ import annotations

from ...data.file_tracking import auto_add_untracked_files
from ...data.hunk_tracking import fetch_next_change
from ...data.session import clear_iteration_state
from ...exceptions import NoMoreHunks
from ...i18n import _
from ..selection.selected_change_display import show_selected_change


def restart_iteration_pass(*, quiet: bool = False) -> None:
    """Clear iteration state and select the first available change."""
    clear_iteration_state()
    auto_add_untracked_files()

    try:
        fetch_next_change()
    except NoMoreHunks:
        if not quiet:
            print(_("No more hunks to process."))
        return

    if not quiet:
        show_selected_change()
