"""History-plan application command."""

from __future__ import annotations

from ..history.execution import start_history_operation
from ..output.rewrite_operation import print_rewrite_operation
from ..utils.git_repository import require_git_repository
from ..utils.session_start_point import require_repository_history


def command_rewrite_apply(
    plan_path: str,
    *,
    allowed_remote_refs: tuple[str, ...],
    porcelain: bool = False,
) -> None:
    """Build, verify, and atomically publish one validated replacement series."""
    require_git_repository()
    require_repository_history()
    state = start_history_operation(
        plan_path,
        allowed_remote_refs=allowed_remote_refs,
    )
    print_rewrite_operation("apply", state, porcelain=porcelain)
