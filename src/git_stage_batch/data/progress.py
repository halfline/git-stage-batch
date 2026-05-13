"""Progress tracking for session state."""

from __future__ import annotations

import json

from ..utils.file_io import count_nonblank_text_file_lines, read_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import (
    get_line_changes_json_file_path,
    get_discarded_hunks_file_path,
    get_included_hunks_file_path,
    get_skipped_hunks_jsonl_file_path,
)


def get_hunk_counts() -> dict[str, int]:
    """Get counts of hunks in various states.

    Returns:
        Dictionary with keys:
        - included: Number of hunks staged to index
        - skipped: Number of hunks explicitly skipped
        - discarded: Number of hunks discarded from working tree
        - remaining: Number of hunks still pending (approximation)
    """
    included_file = get_included_hunks_file_path()
    skipped_file = get_skipped_hunks_jsonl_file_path()
    discarded_file = get_discarded_hunks_file_path()

    # Count included hunks (one per line)
    included_count = count_nonblank_text_file_lines(included_file)

    # Count skipped hunks (JSONL format)
    skipped_count = count_nonblank_text_file_lines(skipped_file)

    # Count discarded hunks (one per line)
    discarded_count = count_nonblank_text_file_lines(discarded_file)

    # Remaining is harder to determine without running diff, so we set to 0
    # The caller should provide this if needed
    remaining_count = 0

    return {
        "included": included_count,
        "skipped": skipped_count,
        "discarded": discarded_count,
        "remaining": remaining_count,
    }


def get_file_progress() -> tuple[int, int]:
    """Get selected file progress.

    Returns:
        Tuple of (selected_file_index, total_files)
        Returns (0, 0) if no selected file is cached
    """
    # Read selected lines to get the file path
    line_changes_file = get_line_changes_json_file_path()
    if not line_changes_file.exists():
        return (0, 0)

    try:
        content = read_text_file_contents(line_changes_file)
        data = json.loads(content)
        selected_path = data.get("path", "")
        if not selected_path:
            return (0, 0)

        # Get all changed files from git diff
        result = run_git_command(
            [
                "-c",
                "diff.ignoreSubmodules=none",
                "diff",
                "--ignore-submodules=none",
                "--name-only",
                "HEAD",
            ],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            return (0, 0)

        files = [f for f in result.stdout.strip().splitlines() if f.strip()]
        total = len(files)

        if selected_path in files:
            # 1-based index
            selected_index = files.index(selected_path) + 1
            return (selected_index, total)
        else:
            # File not in diff
            return (0, total)

    except (json.JSONDecodeError, KeyError):
        return (0, 0)
