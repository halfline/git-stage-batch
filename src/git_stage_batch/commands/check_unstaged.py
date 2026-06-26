"""Read-only checks for unstaged-only batch workflows."""

from __future__ import annotations

import sys

from ..data.staged_renames import (
    list_staged_change_records,
    staged_changes_are_only_normalizable_start_time_changes,
)
from ..exceptions import CommandError
from ..i18n import _, ngettext
from ..utils.git import require_git_repository


def command_check_unstaged() -> None:
    """Check whether the index is suitable for an unstaged-only workflow."""
    require_git_repository()

    staged_records = list_staged_change_records()
    if not staged_records:
        print(_("Index is clean for an unstaged-only workflow."), file=sys.stderr)
        return

    if staged_changes_are_only_normalizable_start_time_changes():
        deletion_count = sum(
            1
            for record in staged_records
            if record.status == "D" and len(record.paths) == 1
        )
        rename_count = len(staged_records) - deletion_count
        if deletion_count and rename_count:
            deletion_text = ngettext(
                "{count} staged deletion",
                "{count} staged deletions",
                deletion_count,
            ).format(count=deletion_count)
            rename_text = ngettext(
                "{count} staged rename",
                "{count} staged renames",
                rename_count,
            ).format(count=rename_count)
            print(
                _(
                    "Index contains {deletions} and {renames} that start "
                    "will treat as workflow content."
                ).format(deletions=deletion_text, renames=rename_text),
                file=sys.stderr,
            )
            return
        if rename_count:
            print(
                ngettext(
                    "Index contains {count} staged rename that start will treat as workflow content.",
                    "Index contains {count} staged renames that start will treat as workflow content.",
                    rename_count,
                ).format(count=rename_count),
                file=sys.stderr,
            )
            return
        print(
            ngettext(
                "Index contains {count} staged deletion that start will treat as workflow content.",
                "Index contains {count} staged deletions that start will treat as workflow content.",
                deletion_count,
            ).format(count=deletion_count),
            file=sys.stderr,
        )
        return

    raise CommandError(
        _(
            "Index contains staged changes outside start-time renames or deletions. "
            "Commit, stash, or unstage them before using the unstaged-only workflow."
        ),
        exit_code=2,
    )
