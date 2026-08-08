"""History-operation continuation command."""

from __future__ import annotations

from ..history.execution import continue_history_operation
from ..output.rewrite_operation import print_rewrite_operation
from ..utils.git_repository import require_git_repository


def command_rewrite_continue(*, porcelain: bool = False) -> None:
    """Resume the exact action recorded by an active checkpoint."""
    require_git_repository()
    state = continue_history_operation()
    print_rewrite_operation("continue", state, porcelain=porcelain)
