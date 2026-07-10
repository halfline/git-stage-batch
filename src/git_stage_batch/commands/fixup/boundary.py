"""Suggest-fixup boundary validation."""

from __future__ import annotations

import subprocess

from ...exceptions import exit_with_error
from ...i18n import _
from ...utils.git_command import run_git_command


def require_suggest_fixup_boundary_range(boundary: str) -> None:
    """Require a valid suggest-fixup boundary with commits through HEAD."""
    try:
        run_git_command(
            ["rev-parse", "--verify", boundary],
            check=True,
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError:
        exit_with_error(_("Invalid boundary ref: {boundary}").format(boundary=boundary))

    try:
        rev_list_result = run_git_command(
            ["rev-list", f"{boundary}..HEAD"],
            check=True,
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError:
        exit_with_error(
            _("Failed to get commit range {boundary}..HEAD").format(
                boundary=boundary,
            )
        )

    if not rev_list_result.stdout.strip():
        exit_with_error(
            _("No commits found in range {boundary}..HEAD").format(
                boundary=boundary,
            )
        )
