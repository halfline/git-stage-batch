"""Remaining hunk estimation for session progress."""

from __future__ import annotations

from dataclasses import asdict

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_jobs import FileJobExecution, run_file_jobs
from ..utils.journal import log_journal
from .live_change_jobs import (
    acquire_live_change_count_plan,
    count_eligible_live_text_file,
)


def estimate_remaining_hunks() -> int:
    """Estimate the number of live hunks not yet included, skipped, or discarded."""
    with acquire_live_change_count_plan() as plan:
        execution = FileJobExecution(
            transport="inline",
            max_workers=1,
            reason="remaining status counting is inline",
        )
        results = run_file_jobs(
            plan.jobs,
            count_eligible_live_text_file,
            execution=execution,
            repository_root=plan.repository_root,
        )
        stale_result = next(
            (result for result in results if result.stale),
            None,
        )
        if stale_result is not None:
            raise CommandError(
                _(
                    "Working tree file changed while status was being "
                    "calculated: {file}. Retry the status command."
                ).format(file=stale_result.file_path)
            )

        for result in results:
            log_journal(
                "file_attribution_complete",
                file_path=result.file_path,
                **asdict(result.attribution_metrics),
            )
        return plan.atomic_count + sum(
            result.eligible_count for result in results
        )
