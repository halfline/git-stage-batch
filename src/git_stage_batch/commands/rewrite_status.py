"""History-operation status command."""

from __future__ import annotations

from ..history.state import (
    inspect_history_operation,
    load_history_operation_for_status,
)
from ..output.rewrite_status import print_rewrite_status
from ..utils.git_repository import require_git_repository


def command_rewrite_status(*, porcelain: bool = False) -> None:
    """Report durable progress and independently checked resume facts."""
    require_git_repository()
    state, active = load_history_operation_for_status()
    inspection = (
        inspect_history_operation(state, require_active=active)
        if state is not None
        else None
    )
    print_rewrite_status(
        state,
        inspection,
        active=active,
        porcelain=porcelain,
    )
