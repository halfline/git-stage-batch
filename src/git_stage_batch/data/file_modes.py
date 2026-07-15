"""Repository file-mode detection."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_repository import get_git_repository_root_path
from ..utils.repository_path import open_repository_path


def detect_file_mode(file_path: str) -> str:
    """Return the current git file mode for a repository path."""
    return detect_file_mode_from_root(get_git_repository_root_path(), file_path)


def detect_file_mode_in_commit(commit: str, file_path: str) -> str | None:
    """Return the file mode for a path in a commit tree, if present."""
    result = run_git_command(
        ["ls-tree", commit, "--", file_path],
        check=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split(maxsplit=1)[0]


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
        literal_pathspecs=True,
    )
    if ls_result.returncode == 0 and ls_result.stdout.strip():
        parts = ls_result.stdout.strip().split()
        if parts:
            return parts[0]
    return "100644"


def apply_git_file_mode(path: Path, file_mode: str | None) -> None:
    """Apply Git executable-bit semantics to an existing worktree path."""
    if file_mode is None or file_mode == "120000":
        return

    try:
        with open_repository_path(
            path,
            access_modes=(os.O_RDONLY, os.O_WRONLY),
        ) as file_descriptor:
            current_mode = os.fstat(file_descriptor).st_mode
            if not stat.S_ISREG(current_mode):
                raise CommandError(
                    _("Cannot apply Git file mode to non-regular path {path}").format(
                        path=path
                    )
                )
            if file_mode == "100755":
                replacement_mode = (
                    current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
            else:
                replacement_mode = current_mode & ~(
                    stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
            os.fchmod(file_descriptor, replacement_mode)
    except CommandError:
        raise
    except OSError as error:
        raise CommandError(
            _("Cannot safely apply Git file mode to {path}: {error}").format(
                path=path,
                error=error,
            )
        ) from error
