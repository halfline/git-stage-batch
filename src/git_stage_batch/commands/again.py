"""Again command implementation."""

from __future__ import annotations

from ..data.auto_advance import write_auto_advance_default
from ..data.session import require_session_started
from ..utils.git_repository import require_git_repository
from ..utils.paths import ensure_state_directory_exists
from .session.iteration import restart_iteration_pass


def command_again(*, quiet: bool = False, auto_advance: bool | None = None) -> None:
    """Clear state and start a fresh pass through all hunks.

    This command resets the iteration-specific state (selected hunk, blocklist,
    snapshots) while preserving permanent state (batches, batch sources, abort
    state, journal). This allows you to make another pass through all hunks
    without losing your batch work.
    """
    require_git_repository()
    require_session_started()
    ensure_state_directory_exists()

    if auto_advance is not None:
        write_auto_advance_default(auto_advance)

    restart_iteration_pass(quiet=quiet)
