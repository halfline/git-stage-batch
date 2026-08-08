"""Create grouped fixup commits from conservatively assigned staged units."""

from __future__ import annotations

from ..data.session_marker import session_is_active
from ..exceptions import CommandError
from ..fixup.execution import execute_fixup_create_plan
from ..fixup.planning import acquire_fixup_create_plan
from ..i18n import _
from ..output.fixup_create import print_fixup_create_output
from ..utils.git_repository import (
    get_git_directory_path,
    require_git_repository,
)
from ..utils.session_start_point import require_repository_history


def _require_no_active_git_operation() -> None:
    git_directory = get_git_directory_path()
    markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-apply",
        "rebase-merge",
        "sequencer",
        "BISECT_START",
    )
    active_operation = next(
        (marker for marker in markers if (git_directory / marker).exists()),
        None,
    )
    if active_operation is not None:
        raise CommandError(
            _(
                "Cannot create fixup commits while another Git operation is "
                "active ({operation})."
            ).format(operation=active_operation)
        )


def command_create_fixups(
    boundary: str | None = None,
    *,
    dry_run: bool = False,
    partial: bool = False,
    porcelain: bool = False,
) -> None:
    """Create one reviewable `fixup!` commit per eligible staged target."""
    require_git_repository()
    require_repository_history()
    if not dry_run:
        if session_is_active():
            raise CommandError(
                _(
                    "Stop the active git-stage-batch session before creating "
                    "fixup commits."
                )
            )
        _require_no_active_git_operation()

    with acquire_fixup_create_plan(boundary) as plan:
        if dry_run:
            print_fixup_create_output(
                plan,
                dry_run=True,
                porcelain=porcelain,
                result=None,
            )
            return

        if plan.remaining_units and not partial:
            print_fixup_create_output(
                plan,
                dry_run=False,
                porcelain=porcelain,
                result=None,
            )
            raise CommandError(
                _(
                    "No commits were created because some staged units are not "
                    "eligible. Inspect the plan or rerun with --partial."
                )
            )
        if not plan.eligible_units:
            print_fixup_create_output(
                plan,
                dry_run=False,
                porcelain=porcelain,
                result=None,
            )
            raise CommandError(_("No staged units have an eligible fixup target."))

        result = execute_fixup_create_plan(plan)
        print_fixup_create_output(
            plan,
            dry_run=False,
            porcelain=porcelain,
            result=result,
        )
