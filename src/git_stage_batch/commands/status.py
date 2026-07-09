"""Status command implementation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ..data.session import session_is_active
from ..data.status_summary import read_status_summary as _read_status_summary
from ..exceptions import CommandError
from ..i18n import _
from ..output.status import print_status_summary as _print_status_summary
from ..output.status_prompt import prompt_needs_status_summary, render_prompt_status
from ..utils.git_command import run_git_command
from ..utils.git_repository import require_git_repository


def _git_directory_for_prompt() -> Path | None:
    """Return the git directory for prompt rendering, or None outside a repo."""
    try:
        result = run_git_command(["rev-parse", "--absolute-git-dir"], check=False, requires_index_lock=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    git_dir = result.stdout.strip()
    return Path(git_dir) if git_dir else None


def command_status(*, porcelain: bool = False, prompt_format: str | None = None) -> None:
    """Show session progress and selected state.

    Args:
        porcelain: If True, output JSON for scripting instead of human-readable text
        prompt_format: If set, render this format string only for active sessions
    """
    if porcelain and prompt_format is not None:
        raise CommandError(_("Cannot use --porcelain with --for-prompt."))

    if prompt_format is not None:
        git_dir = _git_directory_for_prompt()
        if git_dir is None or not session_is_active(git_dir):
            return
    else:
        require_git_repository()

    # Only treat an active abort marker as a live session. The state directory
    # can persist after cleanup because batch metadata is intentionally kept.
    if prompt_format is None and not session_is_active():
        if porcelain:
            print(json.dumps({"session": {"active": False}}))
        else:
            print(_("No batch staging session in progress."), file=sys.stderr)
            print(_("Run 'git-stage-batch start' to begin."), file=sys.stderr)
        return

    if prompt_format is not None:
        output = (
            _read_status_summary()
            if prompt_needs_status_summary(prompt_format)
            else None
        )
        print(render_prompt_status(prompt_format, output), end="")
        return

    output = _read_status_summary()

    if porcelain:
        print(json.dumps(output, indent=2))
    else:
        _print_status_summary(output)
