"""Read-only access to the worktree-local active-session marker."""

from __future__ import annotations

from pathlib import Path

from ..utils.paths import get_state_directory_path


def active_session_marker_path(git_dir: Path | None = None) -> Path:
    """Return the active-session marker path without creating state directories."""
    state_dir = (
        git_dir / "git-stage-batch"
        if git_dir is not None
        else get_state_directory_path()
    )
    return state_dir / "session" / "abort" / "head.txt"


def session_is_active(git_dir: Path | None = None) -> bool:
    """Return whether a batch staging session marker exists."""
    return active_session_marker_path(git_dir).exists()
