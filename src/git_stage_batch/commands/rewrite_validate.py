"""History-plan validation command."""

from __future__ import annotations

from ..history.plan_files import read_and_validate_history_plan
from ..output.rewrite_validate import print_rewrite_validation
from ..utils.git_repository import require_git_repository
from ..utils.session_start_point import require_repository_history


def command_rewrite_validate(
    plan_path: str,
    *,
    porcelain: bool = False,
) -> None:
    """Validate a semantic plan against a regenerated immutable snapshot."""
    require_git_repository()
    require_repository_history()
    document = read_and_validate_history_plan(plan_path)
    print_rewrite_validation(document, porcelain=porcelain)
