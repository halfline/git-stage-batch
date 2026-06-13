"""Read-only checks for unstaged-only batch workflows."""

from __future__ import annotations

import sys

from ..data.staged_renames import list_staged_change_records, staged_changes_are_only_normalizable_renames
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

    if staged_changes_are_only_normalizable_renames():
        rename_count = len(staged_records)
        print(
            ngettext(
                "Index contains {count} staged rename that start will treat as workflow content.",
                "Index contains {count} staged renames that start will treat as workflow content.",
                rename_count,
            ).format(count=rename_count),
            file=sys.stderr,
        )
        return

    raise CommandError(
        _(
            "Index contains staged changes outside start-time renames. "
            "Commit, stash, or unstage them before using the unstaged-only workflow."
        ),
        exit_code=2,
    )
