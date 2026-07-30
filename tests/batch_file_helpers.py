"""Test-only helpers for inspecting stored batch files."""

from __future__ import annotations

from git_stage_batch.batch.state.query import get_batch_commit_sha
from git_stage_batch.utils.git_command import run_git_command


def read_file_from_batch(batch_name: str, file_path: str) -> str | None:
    """Return stored batch text, or None when the batch path is absent."""
    commit_sha = get_batch_commit_sha(batch_name)
    if commit_sha is None:
        return None

    result = run_git_command(
        ["show", f"{commit_sha}:{file_path}"],
        check=False,
        requires_index_lock=False,
    )
    return result.stdout if result.returncode == 0 else None
