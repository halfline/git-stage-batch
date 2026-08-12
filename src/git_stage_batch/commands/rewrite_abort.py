"""History-operation abort command."""

from __future__ import annotations

from ..history.execution import abort_history_operation
from ..output.rewrite_operation import print_rewrite_operation
from ..utils.git_repository import require_git_repository


def command_rewrite_abort(*, porcelain: bool = False) -> None:
    """Restore an owned original branch tip and terminate the operation."""
    require_git_repository()
    state = abort_history_operation()
    print_rewrite_operation("abort", state, porcelain=porcelain)
