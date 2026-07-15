"""Repository path normalization and safe descriptor access."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager, ExitStack
import errno
import os
from dataclasses import dataclass
from pathlib import Path
import stat

from ..exceptions import CommandError
from ..i18n import _
from .git_repository import get_git_repository_root_path


_OPEN_SUPPORTS_DIRECTORY_DESCRIPTORS = os.open in os.supports_dir_fd


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


@contextmanager
def open_repository_path(
    path: str | Path,
    *,
    access_modes: Sequence[int],
) -> Iterator[int]:
    """Open an in-worktree path without following any relative symlink component."""
    valid_access_modes = {os.O_RDONLY, os.O_WRONLY, os.O_RDWR}
    if not access_modes or any(mode not in valid_access_modes for mode in access_modes):
        raise ValueError

    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_only = getattr(os, "O_DIRECTORY", None)
    if (
        no_follow is None
        or directory_only is None
        or not _OPEN_SUPPORTS_DIRECTORY_DESCRIPTORS
    ):
        raise OSError(
            errno.ENOTSUP,
            os.strerror(errno.ENOTSUP),
            os.fspath(path),
        )

    repository_root = get_git_repository_root_path()
    requested_path = Path(path)
    absolute_path = (
        requested_path
        if requested_path.is_absolute()
        else repository_root / requested_path
    )
    try:
        relative_path = absolute_path.relative_to(repository_root)
    except ValueError:
        raise OSError(
            errno.EPERM,
            os.strerror(errno.EPERM),
            os.fspath(path),
        ) from None
    path_parts = relative_path.parts
    if not path_parts or ".." in path_parts:
        raise OSError(
            errno.EPERM,
            os.strerror(errno.EPERM),
            os.fspath(path),
        )

    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_access = getattr(os, "O_PATH", os.O_RDONLY)
    directory_flags = directory_access | directory_only | close_on_exec
    leaf_flags = no_follow | close_on_exec | getattr(os, "O_NONBLOCK", 0)

    with ExitStack() as descriptors:
        directory_descriptor = os.open(repository_root, directory_flags)
        descriptors.callback(os.close, directory_descriptor)
        if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
            raise OSError(
                errno.ENOTDIR,
                os.strerror(errno.ENOTDIR),
                os.fspath(repository_root),
            )

        for component in path_parts[:-1]:
            directory_descriptor = os.open(
                component,
                directory_flags | no_follow,
                dir_fd=directory_descriptor,
            )
            descriptors.callback(os.close, directory_descriptor)
            if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                raise OSError(
                    errno.ENOTDIR,
                    os.strerror(errno.ENOTDIR),
                    component,
                )

        open_error: PermissionError | None = None
        for access_mode in access_modes:
            try:
                file_descriptor = os.open(
                    path_parts[-1],
                    access_mode | leaf_flags,
                    dir_fd=directory_descriptor,
                )
                descriptors.callback(os.close, file_descriptor)
                break
            except PermissionError as error:
                open_error = error
        else:
            assert open_error is not None
            raise open_error

        yield file_descriptor
