"""Git repository location helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..exceptions import exit_with_error
from ..i18n import _
from .git_command import run_git_command


_GIT_REPOSITORY_ROOT_CACHE: dict[Path, Path] = {}
_GIT_DIRECTORY_CACHE: dict[Path, Path] = {}


def require_git_repository() -> None:
    """Verify that we are inside a git repository.

    Calls exit_with_error if not in a git repository, printing git's
    error message for context.

    Raises:
        SystemExit: Via exit_with_error if not in a git repository
    """
    try:
        run_git_command(["rev-parse", "--git-dir"], requires_index_lock=False)
    except subprocess.CalledProcessError as error:
        # Print git's actual error message which contains helpful context
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        exit_with_error(_("Not inside a git repository."), exit_code=error.returncode)


def get_git_repository_root_path() -> Path:
    """Get the absolute path to the git repository root.

    Returns:
        Path object pointing to the repository root directory

    Raises:
        subprocess.CalledProcessError: If not in a git repository
    """
    cwd = Path.cwd()
    cached = _GIT_REPOSITORY_ROOT_CACHE.get(cwd)
    if cached is not None:
        return cached

    output = run_git_command(
        ["rev-parse", "--show-toplevel"],
        requires_index_lock=False,
    ).stdout.strip()
    path = Path(output)
    _GIT_REPOSITORY_ROOT_CACHE[cwd] = path
    return path


def get_git_directory_path() -> Path:
    """Get the absolute path to the repository's git directory."""
    cwd = Path.cwd()
    cached = _GIT_DIRECTORY_CACHE.get(cwd)
    if cached is not None:
        return cached

    output = run_git_command(
        ["rev-parse", "--absolute-git-dir"],
        requires_index_lock=False,
    ).stdout.strip()
    path = Path(output)
    _GIT_DIRECTORY_CACHE[cwd] = path
    return path


def resolve_file_path_to_repo_relative(file_path: str) -> str:
    """Convert a file path to repository-relative format.

    Args:
        file_path: File path to convert

    Returns:
        Repository-relative path, or original path if outside repo
    """
    repo_root = get_git_repository_root_path()
    path = Path(file_path)

    # If it's already relative, use it as-is
    if not path.is_absolute():
        return file_path

    # If it's absolute, make it relative to repo root
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        # Path is outside repo, return as-is
        return file_path
