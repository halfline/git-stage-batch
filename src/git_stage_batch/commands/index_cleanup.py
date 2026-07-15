"""Checked index cleanup shared by destructive command paths."""

from __future__ import annotations

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git_index import git_update_index


def remove_path_from_index(file_path: str) -> None:
    """Force-remove one path from the index or fail the enclosing action."""
    result = git_update_index(
        file_path=file_path,
        force_remove=True,
        check=False,
    )
    if result.returncode != 0:
        exit_with_error(
            _("Failed to remove {file} from the index: {error}").format(
                file=file_path,
                error=result.stderr,
            )
        )
