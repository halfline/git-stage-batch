"""suggest-fixup command infrastructure and helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..i18n import _
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import get_suggest_fixup_state_file_path


def _load_suggest_fixup_state() -> dict[str, Any] | None:
    """Load suggest-fixup state from disk, or None if doesn't exist."""
    state_path = get_suggest_fixup_state_file_path()
    if not state_path.exists():
        return None
    try:
        return json.loads(read_text_file_contents(state_path))
    except (json.JSONDecodeError, KeyError):
        return None


def _save_suggest_fixup_state(state: dict[str, Any]) -> None:
    """Save suggest-fixup state to disk."""
    write_text_file_contents(
        get_suggest_fixup_state_file_path(),
        json.dumps(state, indent=2)
    )


def _reset_suggest_fixup_state() -> None:
    """Clear suggest-fixup state."""
    get_suggest_fixup_state_file_path().unlink(missing_ok=True)


def _should_reset_suggest_fixup_state(
    current_hunk_hash: str,
    line_ids: list[int] | None,
    boundary: str,
    file_path: str,
    min_line: int,
    max_line: int
) -> bool:
    """Check if suggest-fixup state should be reset due to context change."""
    state = _load_suggest_fixup_state()
    if state is None:
        return True

    # Check if any search parameters changed
    return (
        state.get("hunk_hash") != current_hunk_hash or
        state.get("line_ids") != line_ids or
        state.get("boundary") != boundary or
        state.get("file_path") != file_path or
        state.get("min_line") != min_line or
        state.get("max_line") != max_line
    )


def _find_next_fixup_candidate(
    file_path: str,
    min_line: int,
    max_line: int,
    boundary: str,
    last_shown_commit: str | None
) -> str | None:
    """Find the next commit that modified the given line range.

    Returns the commit hash, or None if no more candidates found.
    """
    # Build the git log command
    # If we have a last_shown_commit, search before it
    if last_shown_commit:
        commit_range = f"{boundary}..{last_shown_commit}^"
    else:
        commit_range = f"{boundary}..HEAD"

    try:
        log_result = run_git_command(
            ["log", "-L", f"{min_line},{max_line}:{file_path}", commit_range, "--format=%H", "--max-count=1"],
            check=True
        )
    except subprocess.CalledProcessError:
        return None

    # Parse the first commit (should only be one due to --max-count=1)
    commits = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]
    return commits[0] if commits else None


def _show_commit_diff_for_file(commit_hash: str, file_path: str) -> None:
    """Show the diff from a specific commit for a specific file."""
    try:
        # Show what this commit changed in the file
        show_result = run_git_command(
            ["show", "--format=", "--color=always" if sys.stdout.isatty() else "--color=never", commit_hash, "--", file_path],
            check=True
        )
        if show_result.stdout.strip():
            print()
            print(show_result.stdout.rstrip())
            print()
    except subprocess.CalledProcessError:
        # File might not have been modified in this commit, or other error
        pass
