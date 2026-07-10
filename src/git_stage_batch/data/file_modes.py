"""Repository file-mode detection."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from ..utils.git import get_git_repository_root_path, run_git_command


def detect_file_mode(file_path: str) -> str:
    """Return the current git file mode for a repository path."""
    return detect_file_mode_from_root(get_git_repository_root_path(), file_path)


def detect_file_mode_from_root(repo_root: Path, file_path: str) -> str:
    """Return the current git file mode using a known repository root."""
    absolute_path = repo_root / file_path
    if os.path.lexists(absolute_path):
        file_status = absolute_path.lstat()
        if stat.S_ISLNK(file_status.st_mode):
            return "120000"
        return "100755" if file_status.st_mode & stat.S_IXUSR else "100644"

    ls_result = run_git_command(
        ["ls-files", "-s", "--", file_path],
        check=False,
        requires_index_lock=False,
    )
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            return parts[0]
    return "100644"
