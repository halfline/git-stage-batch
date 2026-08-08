"""History-operation status command."""

from __future__ import annotations

from ..history.state import (
    inspect_history_operation,
    load_active_history_operation,
)
from ..output.rewrite_status import print_rewrite_status
from ..utils.git_repository import require_git_repository


def command_rewrite_status(*, porcelain: bool = False) -> None:
    """Report durable progress and independently checked resume facts."""
    require_git_repository()
    state = load_active_history_operation()
    inspection = inspect_history_operation(state) if state is not None else None
    print_rewrite_status(state, inspection, porcelain=porcelain)
