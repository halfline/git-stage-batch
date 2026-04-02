"""Again command implementation."""

from __future__ import annotations

from ..data.file_tracking import auto_add_untracked_files
from ..data.hunk_tracking import fetch_next_change, show_selected_change
from ..data.session import clear_iteration_state, require_session_started
from ..exceptions import NoMoreHunks
from ..i18n import _
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists


def command_again(*, quiet: bool = False) -> None:
    """Clear state and start a fresh pass through all hunks.

    This command resets the iteration-specific state (selected hunk, blocklist,
    snapshots) while preserving permanent state (batches, batch sources, abort
    state, journal). This allows you to make another pass through all hunks
    without losing your batch work.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    # Clear iteration-specific state (shared logic with stop/abort)
    clear_iteration_state()

    # Auto-add untracked files for fresh pass
    auto_add_untracked_files()

    try:
        fetch_next_change()
    except NoMoreHunks:
        if not quiet:
            print(_("No more hunks to process."))
        return

    if not quiet:
        show_selected_change()
