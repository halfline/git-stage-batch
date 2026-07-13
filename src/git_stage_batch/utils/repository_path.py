"""Lexical normalization of user-supplied repository paths."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..exceptions import CommandError
from ..i18n import _
from .git_repository import get_git_repository_root_path


@dataclass(frozen=True)
class RepositoryPath:
    """One normalized POSIX repository-relative path."""

    value: str
    is_directory: bool


def normalize_repository_path(value: str) -> RepositoryPath:
    """Interpret ``value`` from cwd without resolving symlink components."""
    directory_intent = value.endswith(("/", os.sep))
    repo_root = os.path.abspath(os.fspath(get_git_repository_root_path()))
    candidate = os.path.abspath(value)
    try:
        inside = os.path.commonpath((repo_root, candidate)) == repo_root
    except ValueError:
        inside = False
    if not inside:
        raise CommandError(
            _("Path is outside the repository worktree: {path}").format(path=value)
        )
    relative = os.path.relpath(candidate, repo_root)
    if relative == ".":
        raise CommandError(_("A repository file or directory path is required."))
    normalized = relative.replace(os.sep, "/")
    try:
        present_directory = os.path.isdir(candidate) and not os.path.islink(candidate)
    except OSError:
        present_directory = False
    is_directory = directory_intent or present_directory
    if is_directory:
        normalized = normalized.rstrip("/") + "/"
    return RepositoryPath(normalized, is_directory)
