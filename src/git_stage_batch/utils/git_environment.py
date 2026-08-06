"""Git process environment helpers."""

from __future__ import annotations

import os


def git_environment_with_deterministic_messages(
    env: dict[str, str] | None,
) -> dict[str, str]:
    """Return an environment where Git diagnostics use the C locale."""
    git_env = os.environ.copy() if env is None else dict(env)
    # LC_ALL would otherwise override the category-specific setting.
    git_env.pop("LC_ALL", None)
    git_env["LC_MESSAGES"] = "C"
    return git_env


def git_environment_with_optional_locks_disabled(
    env: dict[str, str] | None,
) -> dict[str, str]:
    """Return an environment that prevents optional Git index refresh locks."""
    git_env = os.environ.copy() if env is None else dict(env)
    git_env["GIT_OPTIONAL_LOCKS"] = "0"
    return git_env
