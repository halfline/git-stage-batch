"""Redo command implementation."""

from __future__ import annotations

import sys

from ..data.session import require_session_started
from ..data.undo import redo_last_checkpoint
from ..i18n import _
from ..utils.git import require_git_repository


def command_redo(*, force: bool = False) -> None:
    """Redo the most recently undone session operation."""
    require_git_repository()
    require_session_started()

    operation = redo_last_checkpoint(force=force)
    print(_("✓ Redid: {operation}").format(operation=operation), file=sys.stderr)
