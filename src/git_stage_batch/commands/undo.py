"""Undo command implementation."""

from __future__ import annotations

import sys

from ..data.session import require_session_started
from ..data.undo import undo_last_checkpoint
from ..i18n import _
from ..utils.git import require_git_repository


def command_undo(*, force: bool = False) -> None:
    """Undo the most recent undoable session operation."""
    require_git_repository()
    require_session_started()

    operation = undo_last_checkpoint(force=force)
    print(_("✓ Undid: {operation}").format(operation=operation), file=sys.stderr)
