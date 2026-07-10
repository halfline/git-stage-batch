"""Repository-wide ownership for worktree-local staging sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git_repository import get_git_directory_path
from ..utils.paths import (
    get_active_session_owner_file_path,
    get_common_state_directory_path,
)
from .session import active_session_marker_path, session_is_active


@dataclass(frozen=True)
class SessionOwner:
    """Identity and marker location for the worktree owning the session."""

    worktree_git_dir: Path
    marker_path: Path
    started_at: str


def _current_worktree_git_dir() -> Path:
    return get_git_directory_path().resolve()


def _current_owner() -> SessionOwner:
    git_dir = _current_worktree_git_dir()
    return SessionOwner(
        worktree_git_dir=git_dir,
        marker_path=active_session_marker_path(git_dir).resolve(),
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def _load_owner() -> SessionOwner | None:
    owner_path = get_active_session_owner_file_path()
    if not owner_path.exists():
        return None
    try:
        data = json.loads(read_text_file_contents(owner_path))
        return SessionOwner(
            worktree_git_dir=Path(data["worktree_git_dir"]).resolve(),
            marker_path=Path(data["marker_path"]).resolve(),
            started_at=str(data["started_at"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise CommandError(
            _(
                "The repository's active-session ownership record is invalid: {path}. "
                "Inspect or remove it only after confirming no worktree has an active session."
            ).format(path=owner_path)
        ) from error


def _write_owner(owner: SessionOwner) -> None:
    write_text_file_contents(
        get_active_session_owner_file_path(),
        json.dumps(
            {
                "version": 1,
                "worktree_git_dir": str(owner.worktree_git_dir),
                "marker_path": str(owner.marker_path),
                "started_at": owner.started_at,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
    )


def _owner_is_current_worktree(owner: SessionOwner) -> bool:
    return owner.worktree_git_dir == _current_worktree_git_dir()


def _discard_stale_owner(owner: SessionOwner) -> bool:
    """Remove an owner whose worktree-local session marker no longer exists."""
    if owner.marker_path.exists():
        return False
    from .recovery_anchors import clear_recovery_anchors

    clear_recovery_anchors()
    get_active_session_owner_file_path().unlink(missing_ok=True)
    return True


def _foreign_owner_error(owner: SessionOwner) -> CommandError:
    return CommandError(
        _(
            "Another linked worktree has an active git-stage-batch session "
            "({git_dir}, started {started_at}). Stop or abort that session before "
            "mutating shared batch state from this worktree."
        ).format(
            git_dir=owner.worktree_git_dir,
            started_at=owner.started_at,
        )
    )


def require_no_foreign_session_owner() -> None:
    """Refuse mutation while a live session belongs to another worktree."""
    owner = _load_owner()
    if owner is None or _owner_is_current_worktree(owner):
        return
    if _discard_stale_owner(owner):
        return
    raise _foreign_owner_error(owner)


def claim_session_ownership() -> None:
    """Publish the current worktree as owner of its completed abort snapshot."""
    if not session_is_active():
        raise CommandError(
            _("Cannot claim session ownership before the recovery snapshot is complete.")
        )
    require_no_foreign_session_owner()
    owner = _load_owner()
    if owner is not None and _owner_is_current_worktree(owner):
        return
    _write_owner(_current_owner())


def require_current_session_owner() -> None:
    """Require the current worktree to own its active session.

    A session created by an older version has no common ownership record. It
    is claimed lazily while the repository-wide lock is held by normal CLI
    dispatch.
    """
    if not session_is_active():
        raise CommandError(_("No session in progress. Run 'git-stage-batch start' first."))
    require_no_foreign_session_owner()
    owner = _load_owner()
    if owner is None:
        _write_owner(_current_owner())
        return
    if not _owner_is_current_worktree(owner):
        raise _foreign_owner_error(owner)


def release_session_ownership() -> None:
    """Remove ownership after the current worktree completes stop or abort."""
    owner = _load_owner()
    if owner is None:
        return
    if not _owner_is_current_worktree(owner):
        raise _foreign_owner_error(owner)
    get_active_session_owner_file_path().unlink(missing_ok=True)
    common_state_dir = get_common_state_directory_path()
    try:
        common_state_dir.rmdir()
    except OSError:
        # The common lock, shared state, or another durable file still exists.
        pass
