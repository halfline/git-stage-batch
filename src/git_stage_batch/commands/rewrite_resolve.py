"""External rewrite-resolution workspace command."""

from __future__ import annotations

from ..history.resolution_workspace import resolve_history_plan
from ..output.rewrite_resolution import print_rewrite_resolution
from ..utils.git_repository import require_git_repository
from ..utils.session_start_point import require_repository_history


def command_rewrite_resolve(
    plan_path: str,
    *,
    workspace_path: str,
    accept_result: bool = False,
    porcelain: bool = False,
) -> None:
    """Create or advance an external resolution workspace."""
    require_git_repository()
    require_repository_history()
    result = resolve_history_plan(
        plan_path,
        workspace_path,
        accept_result=accept_result,
    )
    print_rewrite_resolution(
        result,
        porcelain=porcelain,
    )
