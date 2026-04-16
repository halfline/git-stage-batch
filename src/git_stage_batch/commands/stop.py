"""Stop command implementation."""

from __future__ import annotations

import sys

from ..data.session import clear_session_state
from ..i18n import _
from ..utils.file_io import read_file_paths_file
from ..utils.git import require_git_repository, run_git_command
from ..utils.paths import get_auto_added_files_file_path


def command_stop() -> None:
    """Stop the selected batch staging session."""
    require_git_repository()

    # Undo auto-added files before clearing state
    auto_added_path = get_auto_added_files_file_path()
    if auto_added_path.exists():
        auto_added = read_file_paths_file(auto_added_path)
        for file_path in auto_added:
            run_git_command(["reset", "--", file_path], check=False)

    # Clear all session state (preserves batches and batch-sources)
    clear_session_state()

    print(_("✓ State cleared."), file=sys.stderr)
