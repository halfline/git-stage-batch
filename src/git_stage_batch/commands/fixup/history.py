"""Git history helpers for suggest-fixup commands."""

from __future__ import annotations

import subprocess
import sys

from ...utils.git_command import run_git_command


def get_commit_details(commit_hash: str) -> dict[str, str]:
    """Return commit details used by porcelain suggest-fixup output."""
    try:
        show_result = run_git_command(
            [
                "show",
                "--no-patch",
                "--format=%h%n%H%n%s%n%an%n%ai%n%ar",
                commit_hash,
            ],
            check=True,
            requires_index_lock=False,
        )
        lines = show_result.stdout.strip().split("\n")
        if len(lines) >= 6:
            return {
                "hash": lines[0],
                "full_hash": lines[1],
                "subject": lines[2],
                "author": lines[3],
                "date": lines[4],
                "relative_date": lines[5],
            }
    except subprocess.CalledProcessError:
        pass

    return {
        "hash": commit_hash[:7] if len(commit_hash) > 7 else commit_hash,
        "full_hash": commit_hash,
        "subject": "",
        "author": "",
        "date": "",
        "relative_date": "",
    }


def find_next_fixup_candidate(
    file_path: str,
    min_line: int,
    max_line: int,
    boundary: str,
    last_shown_commit: str | None,
) -> str | None:
    """Return the next commit that modified the given line range."""
    if last_shown_commit:
        commit_range = f"{boundary}..{last_shown_commit}^"
    else:
        commit_range = f"{boundary}..HEAD"

    try:
        log_result = run_git_command(
            [
                "log",
                "-L",
                f"{min_line},{max_line}:{file_path}",
                commit_range,
                "--format=%H",
                "--max-count=1",
            ],
            check=True,
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError:
        return None

    commits = [
        line.strip()
        for line in log_result.stdout.splitlines()
        if line.strip()
    ]
    return commits[0] if commits else None


def show_commit_diff_for_file(commit_hash: str, file_path: str) -> None:
    """Print the diff from a specific commit for a specific file."""
    try:
        show_result = run_git_command(
            [
                "show",
                "--format=",
                "--color=always" if sys.stdout.isatty() else "--color=never",
                commit_hash,
                "--",
                file_path,
            ],
            check=True,
            requires_index_lock=False,
        )
        if show_result.stdout.strip():
            print()
            print(show_result.stdout.rstrip())
            print()
    except subprocess.CalledProcessError:
        pass
