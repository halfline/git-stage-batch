"""Start command implementation."""

from __future__ import annotations

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git import require_git_repository
from ..utils.paths import ensure_state_directory_exists


def command_start() -> None:
    """Start a new batch staging session."""
    require_git_repository()
    ensure_state_directory_exists()
