"""Start command implementation."""

from __future__ import annotations

from typing import Optional

from ..data.auto_advance import DEFAULT_AUTO_ADVANCE, write_auto_advance_default
from ..data.hunk_tracking import fetch_next_change
from ..data.selected_change.lifecycle import clear_selected_change_state_files
from ..data.file_tracking import auto_add_untracked_files
from ..data.session import initialize_abort_state, session_is_active
from ..data.staged_renames import normalize_start_time_staged_deletions, normalize_start_time_staged_renames
from ..exceptions import CommandError, NoMoreHunks
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git_repository import require_git_repository
from ..utils.paths import ensure_state_directory_exists, get_context_lines_file_path
from .selection.selected_change_display import show_selected_change
from .session.iteration import restart_iteration_pass


def command_start(
    *,
    context_lines: Optional[int] = None,
    quiet: bool = False,
    auto_advance: bool | None = None,
) -> None:
    """Start a new batch staging session.

    Args:
        context_lines: Number of context lines to use in diffs (default: 3)
        quiet: If True, suppress "No more hunks" message when no changes exist
        auto_advance: Whether later actions should select the next hunk
    """
    require_git_repository()
    ensure_state_directory_exists()

    # If session already exists, run again logic instead
    if session_is_active():
        if auto_advance is not None:
            write_auto_advance_default(auto_advance)
        restart_iteration_pass(quiet=quiet)
        return

    # Batch reviews may be shown outside an active session. A new session must
    # not inherit that selected/review cache before selecting the first live hunk.
    clear_selected_change_state_files()

    # Initialize abort state for new session
    initialize_abort_state()

    # Staged renames and text deletions are already in the index, so a plain
    # worktree-vs-index pass would otherwise never offer them as workflow
    # choices. Preserve the exact start state in abort metadata first, then
    # expose those staged entries as unstaged choices for this session.
    normalize_start_time_staged_renames()
    normalize_start_time_staged_deletions()

    write_auto_advance_default(
        DEFAULT_AUTO_ADVANCE
        if auto_advance is None else
        auto_advance
    )

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
