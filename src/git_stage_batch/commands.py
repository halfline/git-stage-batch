"""Command implementations for git-stage-batch."""

from __future__ import annotations

import shutil

from .i18n import _
from .state import ensure_state_directory_exists, get_state_directory_path, require_git_repository


def command_start() -> None:
    """Start a new batch staging session."""
    require_git_repository()
    ensure_state_directory_exists()


def command_stop() -> None:
    """Stop the current batch staging session."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    print(_("✓ State cleared."))


def command_again() -> None:
    """Clear state and start a fresh pass through all hunks."""
    require_git_repository()
    state_dir = get_state_directory_path()
    if state_dir.exists():
        shutil.rmtree(state_dir)
    ensure_state_directory_exists()
