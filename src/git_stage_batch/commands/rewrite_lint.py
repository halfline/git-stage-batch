"""Offline frozen rewrite-plan lint command."""

from __future__ import annotations

from ..exceptions import CommandError
from ..history.plan_files import read_and_lint_frozen_history_plan
from ..output.rewrite_lint import (
    print_rewrite_lint,
    print_rewrite_lint_document_failure,
)


def command_rewrite_lint(plan_path: str, *, porcelain: bool = False) -> None:
    """Report all independent static plan failures before expensive work."""
    try:
        result = read_and_lint_frozen_history_plan(plan_path)
    except CommandError as error:
        if not porcelain:
            raise
        print_rewrite_lint_document_failure(error.message)
        raise CommandError("", exit_code=error.exit_code) from error
    print_rewrite_lint(result, porcelain=porcelain)
    if not result.valid:
        raise CommandError("", exit_code=1)
