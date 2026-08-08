"""Independent history-operation verification command."""

from __future__ import annotations

from ..history.execution import verify_history_operation
from ..output.rewrite_operation import print_rewrite_operation
from ..utils.git_repository import require_git_repository


def command_rewrite_verify(*, porcelain: bool = False) -> None:
    """Rebuild proof for the active or latest complete operation."""
    require_git_repository()
    state, verification = verify_history_operation()
    print_rewrite_operation(
        "verify",
        state,
        verification=verification,
        porcelain=porcelain,
    )
