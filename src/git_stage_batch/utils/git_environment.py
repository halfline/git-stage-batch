"""Git process environment helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class _GitIndexOverride:
    """An index path and optional caller environment for nested Git commands."""

    path: str
    env: dict[str, str] | None
    config: tuple[tuple[str, str], ...]
    worktree_root: Path | None


_ACTIVE_GIT_INDEX_OVERRIDE: ContextVar[_GitIndexOverride | None] = ContextVar(
    "git_stage_batch_active_git_index_override",
    default=None,
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
