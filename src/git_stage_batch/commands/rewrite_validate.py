"""History-plan validation command."""

from __future__ import annotations

from ..history.plan_files import (
    read_and_validate_history_plan,
    read_and_validate_history_plan_semantics,
)
from ..history.resolution_workspace import (
    HistoryAuthenticatedResolution,
    materialize_completed_history_resolution,
)
from ..output.rewrite_validate import print_rewrite_validation
from ..utils.git_object_io import temporary_git_object_environment
from ..utils.git_repository import require_git_repository
from ..utils.session_start_point import require_repository_history


def command_rewrite_validate(
    plan_path: str,
    *,
    resolutions_path: str | None = None,
    porcelain: bool = False,
) -> None:
    """Validate a semantic plan against independently reacquired source facts."""
    require_git_repository()
    require_repository_history()
    resolution: HistoryAuthenticatedResolution | None = None
    if resolutions_path is None:
        document = read_and_validate_history_plan(plan_path)
    else:
        document, raw_plan_sha256 = read_and_validate_history_plan_semantics(plan_path)
        with temporary_git_object_environment(
            disable_replace_objects=True
        ) as quarantine:
            resolution = materialize_completed_history_resolution(
                document,
                raw_plan_sha256,
                resolutions_path,
                quarantine=quarantine,
            )
        if (
            resolution.raw_plan_sha256 != raw_plan_sha256
            or resolution.replay.final_tree != document.snapshot.final_tree
        ):
            raise AssertionError("authenticated resolution provenance changed")
    print_rewrite_validation(
        document,
        resolution=resolution,
        porcelain=porcelain,
    )
