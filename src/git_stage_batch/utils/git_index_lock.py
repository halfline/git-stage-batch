"""Git index-lock waiting helpers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .command import run_command
from .git_environment import git_environment_with_optional_locks_disabled


DEFAULT_INDEX_LOCK_WAIT_SECONDS = 20.0
DEFAULT_INDEX_LOCK_POLL_SECONDS = 0.05


def _custom_index_lock_path(
    *,
    env: dict[str, str] | None,
    cwd: str | None,
) -> Path | None:
    git_env = os.environ.copy() if env is None else dict(env)
    index_file = git_env.get("GIT_INDEX_FILE")
    if not index_file:
        return None

    index_path = Path(index_file)
    if not index_path.is_absolute():
        index_path = (Path.cwd() if cwd is None else Path(cwd)) / index_path
    return Path(f"{index_path}.lock")


def _git_index_lock_path(*, cwd: str | None, env: dict[str, str] | None) -> Path:
    custom_index_lock_path = _custom_index_lock_path(env=env, cwd=cwd)
    if custom_index_lock_path is not None:
        return custom_index_lock_path

    result = run_command(
        ["git", "rev-parse", "--absolute-git-dir"],
        check=True,
        cwd=cwd,
        env=git_environment_with_optional_locks_disabled(env),
    )
    return Path(result.stdout.strip()) / "index.lock"


def wait_for_git_index_lock(
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = DEFAULT_INDEX_LOCK_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_INDEX_LOCK_POLL_SECONDS,
) -> None:
    """Wait briefly for a pre-existing Git index lock to disappear."""
    try:
        index_lock_path = _git_index_lock_path(cwd=cwd, env=env)
    except subprocess.CalledProcessError:
        return

    deadline = time.monotonic() + timeout_seconds
    while index_lock_path.exists():
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return
        time.sleep(min(poll_seconds, remaining_seconds))
