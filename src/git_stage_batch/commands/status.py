"""Status command implementation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ..data.session_marker import session_is_active
from ..data.status_summary import read_status_summary as _read_status_summary
from ..exceptions import CommandError
from ..i18n import _
from ..output.status import print_status_summary as _print_status_summary
from ..output.status_prompt import prompt_needs_status_summary, render_prompt_status
from ..utils.git_repository import get_git_directory_path, require_git_repository
from .status_cache import (
    cache_exact_prompt_status_if_requested,
    read_prompt_status_from_cache,
    refresh_status_summary_cache,
    request_status_summary_cache_refresh,
)


def _git_directory_for_prompt() -> Path | None:
    """Return the git directory for prompt rendering, or None outside a repo."""
    try:
        return get_git_directory_path()
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def command_status(
    *,
    porcelain: bool = False,
    prompt_format: str | None = None,
    refresh_cache: bool = False,
    schedule_prompt_cache_refresh: bool = False,
) -> None:
    """Show session progress and selected state.

    Args:
        porcelain: If True, output JSON for scripting instead of human-readable text
        prompt_format: If set, render this format string only for active sessions
        refresh_cache: Rebuild the prompt cache without producing output
        schedule_prompt_cache_refresh: Start a worker after a provisional prompt read
    """
    if refresh_cache and (porcelain or prompt_format is not None):
        raise CommandError(_("Cache refresh cannot be combined with status output."))
    if porcelain and prompt_format is not None:
        raise CommandError(_("Cannot use --porcelain with --for-prompt."))

    if refresh_cache:
        require_git_repository()
        if session_is_active():
            refresh_status_summary_cache()
        return

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
        assert git_dir is not None
        needs_summary = prompt_needs_status_summary(prompt_format)
        snapshot = (
            read_prompt_status_from_cache(git_dir)
            if needs_summary
            else None
        )
        if snapshot is not None and snapshot.needs_refresh:
            if schedule_prompt_cache_refresh:
                request_status_summary_cache_refresh()
        output = snapshot.summary if snapshot is not None else None
        if needs_summary and output is None:
            return
        print(render_prompt_status(prompt_format, output), end="")
        return

    output = _read_status_summary()
    cache_exact_prompt_status_if_requested(output)

    if porcelain:
        print(json.dumps(output, indent=2))
    else:
        _print_status_summary(output)
