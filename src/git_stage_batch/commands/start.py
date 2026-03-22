"""Start command implementation."""

from __future__ import annotations

from typing import Optional

from ..data.file_tracking import auto_add_untracked_files
from ..data.session import initialize_abort_state
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists, get_context_lines_file_path


def command_start(*, context_lines: Optional[int] = None) -> None:
    """Start a new batch staging session.

    Args:
        context_lines: Number of context lines to use in diffs (default: 3)
    """
    require_git_repository()
    ensure_state_directory_exists()

    # Initialize abort state for new session
    initialize_abort_state()

    # Save context lines setting if provided
    if context_lines is not None:
        write_text_file_contents(get_context_lines_file_path(), str(context_lines))

    # Make untracked files visible to git diff so they can be staged, blocked by .gitignore, or deleted
    auto_add_untracked_files()
