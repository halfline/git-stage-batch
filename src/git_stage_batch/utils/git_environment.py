"""Git process environment helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat
import sys

from .git_descriptor_exec import (
    DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR,
    DARWIN_OBJECT_DIRECTORY_DESCRIPTOR,
)


@dataclass(frozen=True)
class _GitIndexOverride:
    """An index path and optional caller environment for nested Git commands."""

    path: str
    env: dict[str, str] | None
    config: tuple[tuple[str, str], ...]
    worktree_root: Path | None


@dataclass(frozen=True, slots=True)
class _GitObjectEnvironmentPin:
    """Borrowed descriptors for one product-issued Git object environment."""

    marker: str
    object_directory_path: str
    alternate_object_directory_path: str
    object_directory_descriptor: int
    object_directory_device: int
    object_directory_inode: int
    alternate_object_directory_descriptor: int
    alternate_object_directory_device: int
    alternate_object_directory_inode: int


_ACTIVE_GIT_INDEX_OVERRIDE: ContextVar[_GitIndexOverride | None] = ContextVar(
    "git_stage_batch_active_git_index_override",
    default=None,
)
_ACTIVE_GIT_OBJECT_DIRECTORY_PINS: ContextVar[tuple[_GitObjectEnvironmentPin, ...]] = (
    ContextVar(
        "git_stage_batch_active_git_object_directory_pins",
        default=(),
    )
)
_PINNED_GIT_OBJECT_ENVIRONMENT_MARKER = "GIT_STAGE_BATCH_PINNED_OBJECT_ENVIRONMENT"


def _inherited_file_descriptor_path(descriptor: int) -> str:
    if sys.platform == "linux":
        return f"/proc/self/fd/{descriptor}"
    raise OSError(
        errno.ENOTSUP,
        "descriptor-pinned Git object directories are unsupported",
    )


def _require_pinned_directory(
    descriptor: int,
    device: int,
    inode: int,
    *,
    location: str,
) -> None:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeError(f"Cannot authenticate {location}: {error}") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_dev != device
        or metadata.st_ino != inode
    ):
        raise RuntimeError(f"The {location} identity changed")


def _require_git_object_environment_pin(pin: _GitObjectEnvironmentPin) -> None:
    _require_pinned_directory(
        pin.object_directory_descriptor,
        pin.object_directory_device,
        pin.object_directory_inode,
        location="pinned Git object directory",
    )
    _require_pinned_directory(
        pin.alternate_object_directory_descriptor,
        pin.alternate_object_directory_device,
        pin.alternate_object_directory_inode,
        location="pinned alternate Git object directory",
    )


@contextmanager
def pin_git_object_environment(
    environment: Mapping[str, str],
    object_directory_descriptor: int,
    alternate_object_directory_descriptor: int,
) -> Iterator[dict[str, str]]:
    """Yield one scoped environment backed only by borrowed directory FDs."""
    if _PINNED_GIT_OBJECT_ENVIRONMENT_MARKER in environment:
        raise ValueError("the Git object environment is already pinned")
    for descriptor in (
        object_directory_descriptor,
        alternate_object_directory_descriptor,
    ):
        if descriptor < 3:
            raise ValueError("Git object directory descriptors must be at least 3")
    try:
        object_metadata = os.fstat(object_directory_descriptor)
        alternate_metadata = os.fstat(alternate_object_directory_descriptor)
    except OSError as error:
        raise RuntimeError(
            f"Cannot inspect a pinned Git object directory: {error}"
        ) from error
    if not stat.S_ISDIR(object_metadata.st_mode) or not stat.S_ISDIR(
        alternate_metadata.st_mode
    ):
        raise ValueError("Git object directory descriptors must name directories")
    object_directory_path = (
        _inherited_file_descriptor_path(object_directory_descriptor)
        if sys.platform != "darwin"
        else ""
    )
    alternate_object_directory_path = (
        _inherited_file_descriptor_path(alternate_object_directory_descriptor)
        if sys.platform != "darwin"
        else ""
    )
    marker = ":".join(
        (
            str(object_directory_descriptor),
            f"{object_metadata.st_dev:x}",
            f"{object_metadata.st_ino:x}",
            str(alternate_object_directory_descriptor),
            f"{alternate_metadata.st_dev:x}",
            f"{alternate_metadata.st_ino:x}",
        )
    )
    pin = _GitObjectEnvironmentPin(
        marker=marker,
        object_directory_path=object_directory_path,
        alternate_object_directory_path=alternate_object_directory_path,
        object_directory_descriptor=object_directory_descriptor,
        object_directory_device=object_metadata.st_dev,
        object_directory_inode=object_metadata.st_ino,
        alternate_object_directory_descriptor=alternate_object_directory_descriptor,
        alternate_object_directory_device=alternate_metadata.st_dev,
        alternate_object_directory_inode=alternate_metadata.st_ino,
    )
    token = _ACTIVE_GIT_OBJECT_DIRECTORY_PINS.set(
        (*_ACTIVE_GIT_OBJECT_DIRECTORY_PINS.get(), pin)
    )
    try:
        pinned_environment = dict(environment)
        pinned_environment[_PINNED_GIT_OBJECT_ENVIRONMENT_MARKER] = marker
        yield pinned_environment
    finally:
        _ACTIVE_GIT_OBJECT_DIRECTORY_PINS.reset(token)


def _active_git_object_environment_pin(
    env: Mapping[str, str],
) -> _GitObjectEnvironmentPin | None:
    marker = env.get(_PINNED_GIT_OBJECT_ENVIRONMENT_MARKER)
    if marker is None:
        return None
    for pin in reversed(_ACTIVE_GIT_OBJECT_DIRECTORY_PINS.get()):
        if marker == pin.marker:
            return pin
    raise RuntimeError("The pinned Git object environment is no longer active")


def require_pinned_git_object_environment(env: Mapping[str, str]) -> None:
    """Require one environment to carry a live product-issued object pin."""
    pin = _active_git_object_environment_pin(env)
    if pin is None:
        raise ValueError("a pinned Git object environment is required")
    _require_git_object_environment_pin(pin)


def git_environment_with_pinned_object_store(
    env: dict[str, str] | None,
) -> tuple[dict[str, str] | None, tuple[int, ...]]:
    """Resolve one scoped object capability into child paths and borrowed FDs."""
    if env is None:
        return None, ()
    pin = _active_git_object_environment_pin(env)
    if pin is None:
        return env, ()
    _require_git_object_environment_pin(pin)
    pinned_environment = env.copy()
    pinned_environment.pop(_PINNED_GIT_OBJECT_ENVIRONMENT_MARKER, None)
    if sys.platform == "darwin":
        pinned_environment[DARWIN_OBJECT_DIRECTORY_DESCRIPTOR] = str(
            pin.object_directory_descriptor
        )
        pinned_environment[DARWIN_ALTERNATE_OBJECT_DIRECTORY_DESCRIPTOR] = str(
            pin.alternate_object_directory_descriptor
        )
    else:
        pinned_environment["GIT_OBJECT_DIRECTORY"] = pin.object_directory_path
        pinned_environment["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = (
            pin.alternate_object_directory_path
        )
    pinned_environment.pop("GIT_QUARANTINE_PATH", None)
    return pinned_environment, (
        pin.object_directory_descriptor,
        pin.alternate_object_directory_descriptor,
    )


def git_environment_with_active_index(
    env: dict[str, str] | None,
    *,
    cwd: str | None = None,
) -> dict[str, str]:
    """Copy an environment and apply the current same-worktree index override."""
    override = _ACTIVE_GIT_INDEX_OVERRIDE.get()
    if override is None:
        return os.environ.copy() if env is None else dict(env)

    if env is not None:
        git_env = dict(env)
    elif override.env is not None:
        git_env = dict(override.env)
    else:
        git_env = os.environ.copy()

    if not _cwd_uses_index_override(override, cwd=cwd):
        if env is None:
            git_env.pop("GIT_INDEX_FILE", None)
        return git_env

    # An explicit alternate index is a complete opt-out. A partial explicit
    # environment still belongs to the surrounding transaction.
    if env is not None and "GIT_INDEX_FILE" in env:
        return git_env
    git_env["GIT_INDEX_FILE"] = override.path
    try:
        config_count = int(git_env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        config_count = 0
    for offset, (key, value) in enumerate(override.config):
        position = config_count + offset
        git_env[f"GIT_CONFIG_KEY_{position}"] = key
        git_env[f"GIT_CONFIG_VALUE_{position}"] = value
    git_env["GIT_CONFIG_COUNT"] = str(config_count + len(override.config))
    return git_env


def _cwd_uses_index_override(
    override: _GitIndexOverride,
    *,
    cwd: str | None,
) -> bool:
    """Return whether a Git command belongs to the overridden worktree."""
    worktree_root = override.worktree_root
    if worktree_root is None:
        return True

    command_directory = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    try:
        command_directory.relative_to(worktree_root)
    except ValueError:
        return False

    current = command_directory
    while current != worktree_root:
        if os.path.lexists(current / ".git"):
            return False
        parent = current.parent
        if parent == current:
            return False
        current = parent
    return True


@contextmanager
def use_git_index_file(
    path: Path,
    *,
    env: dict[str, str] | None = None,
    config: Mapping[str, str] | None = None,
    worktree_root: Path | None = None,
) -> Iterator[None]:
    """Route implicit Git command environments to one alternate index."""
    override = _GitIndexOverride(
        str(path),
        None if env is None else dict(env),
        tuple(config.items()) if config is not None else (),
        worktree_root.resolve() if worktree_root is not None else None,
    )
    token = _ACTIVE_GIT_INDEX_OVERRIDE.set(override)
    try:
        yield
    finally:
        _ACTIVE_GIT_INDEX_OVERRIDE.reset(token)


def git_environment_with_deterministic_messages(
    env: dict[str, str] | None,
    *,
    cwd: str | None = None,
) -> dict[str, str]:
    """Return an environment where Git diagnostics use the C locale."""
    git_env = git_environment_with_active_index(env, cwd=cwd)
    # LC_ALL would otherwise override the category-specific setting.
    git_env.pop("LC_ALL", None)
    git_env["LC_MESSAGES"] = "C"
    return git_env


def git_environment_with_optional_locks_disabled(
    env: dict[str, str] | None,
    *,
    cwd: str | None = None,
) -> dict[str, str]:
    """Return an environment that prevents optional Git index refresh locks."""
    git_env = git_environment_with_active_index(env, cwd=cwd)
    git_env["GIT_OPTIONAL_LOCKS"] = "0"
    return git_env
