"""Start command implementation."""

from __future__ import annotations

from ..data.hunk_tracking import find_and_cache_next_unblocked_hunk, show_current_hunk
from ..data.file_tracking import auto_add_untracked_files
from ..data.session import initialize_abort_state
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists


def command_start(*, quiet: bool = False) -> None:
    """Start a new batch staging session.

    Args:
        quiet: If True, suppress "No more hunks" message when no changes exist
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Initialize abort state for new session
    initialize_abort_state()

    # Make untracked files visible to git diff so they can be staged, blocked by .gitignore, or deleted
    auto_add_untracked_files()

    # Find and cache first hunk
    if find_and_cache_next_unblocked_hunk() is None:
        raise CommandError(_("No changes to process."), exit_code=2)

    # Display the first hunk
    if not quiet:
        show_current_hunk()
