"""File change status queries."""

from __future__ import annotations

from ..utils.git_command import run_git_command


def file_has_staged_changes(file_path: str) -> bool:
    """Return whether the index version of a path differs from HEAD."""
    result = run_git_command(
        [
            "diff",
            "--cached",
            "--quiet",
            "--no-renames",
            "--",
            file_path,
        ],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 1


def file_has_unstaged_changes(file_path: str) -> bool:
    """Return whether the working tree version of a path differs from the index."""
    result = run_git_command(
        [
            "diff",
            "--quiet",
            "--no-renames",
            "--",
            file_path,
        ],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 1
