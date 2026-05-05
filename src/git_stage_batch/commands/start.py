"""Start command implementation."""

from __future__ import annotations

from typing import Optional

from .again import command_again
from ..data.hunk_tracking import clear_selected_change_state_files, fetch_next_change, show_selected_change
from ..data.file_tracking import auto_add_untracked_files
from ..data.session import initialize_abort_state
from ..exceptions import CommandError, NoMoreHunks
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists, get_context_lines_file_path, get_abort_head_file_path


def command_start(*, context_lines: Optional[int] = None, quiet: bool = False) -> None:
    """Start a new batch staging session.

    Args:
        context_lines: Number of context lines to use in diffs (default: 3)
        quiet: If True, suppress "No more hunks" message when no changes exist
    """
    require_git_repository()
    ensure_state_directory_exists()

    # If session already exists, run again logic instead
    if get_abort_head_file_path().exists():
        command_again(quiet=quiet)
        return

    # Batch reviews may be shown outside an active session. A new session must
    # not inherit that selected/review cache before selecting the first live hunk.
    clear_selected_change_state_files()

    # Initialize abort state for new session
    initialize_abort_state()

    # Save context lines setting if provided
    if context_lines is not None:
        write_text_file_contents(get_context_lines_file_path(), str(context_lines))

    # Make untracked files visible to git diff so they can be staged, blocked by .gitignore, or deleted
    auto_add_untracked_files()

    # Find and cache first hunk
    try:
        fetch_next_change()
    except NoMoreHunks:
        raise CommandError(_("No changes to process."), exit_code=2)

    # Display the first hunk
    if not quiet:
        show_selected_change()
