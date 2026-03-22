"""Stop command implementation."""

from __future__ import annotations

import shutil
import sys

from ..i18n import _
from ..utils.git import require_git_repository, run_git_command
from ..utils.paths import get_state_directory_path


def command_stop() -> None:
    """Stop the selected batch staging session."""
    require_git_repository()

    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(_("✓ State cleared."), file=sys.stderr)
