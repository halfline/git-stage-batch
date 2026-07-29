"""Declarative runtime policy for registered CLI commands."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum


class SessionOwnershipPolicy(Enum):
    """Whether a command may run while another worktree owns the session."""

    REQUIRE_AVAILABLE = "require-available"
    ALLOW_FOREIGN = "allow-foreign"


class LockingPolicy(Enum):
    """How the top-level CLI acquires the repository session lock."""

    SESSION = "session"
    NONE = "none"
    SESSION_EXCEPT_PROMPT = "session-except-prompt"


class RepositoryPolicy(Enum):
    """Whether a command requires a Git repository."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    OPTIONAL_FOR_PROMPT = "optional-for-prompt"


class PagerPolicy(Enum):
    """Whether human-readable output may use the configured pager."""

    ELIGIBLE = "eligible"
    NEVER = "never"


class StateChangePolicy(Enum):
    """The most persistent application or repository state a command may change."""

    NONE = "none"
    SCRATCH = "scratch"
    DURABLE = "durable"


@dataclass(frozen=True)
class CommandPolicy:
    """Complete runtime contract attached to one registered CLI command."""

    session_ownership: SessionOwnershipPolicy
    locking: LockingPolicy
    repository: RepositoryPolicy
    pager: PagerPolicy
    state_changes: StateChangePolicy


CONSERVATIVE_COMMAND_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.REQUIRE_AVAILABLE,
    locking=LockingPolicy.SESSION,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
    state_changes=StateChangePolicy.DURABLE,
)

IMPLICIT_SHOW_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.REQUIRE_AVAILABLE,
    locking=LockingPolicy.SESSION,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.ELIGIBLE,
    state_changes=StateChangePolicy.SCRATCH,
)

INTERACTIVE_POLICY = CommandPolicy(
    session_ownership=SessionOwnershipPolicy.REQUIRE_AVAILABLE,
    locking=LockingPolicy.NONE,
    repository=RepositoryPolicy.REQUIRED,
    pager=PagerPolicy.NEVER,
    state_changes=StateChangePolicy.DURABLE,
)


def policy_for_args(args: argparse.Namespace) -> CommandPolicy:
    """Return declared policy, failing closed for synthetic or unknown arguments."""
    if (
        getattr(args, "interactive_flag", False)
        or getattr(args, "interactive_command", False)
    ):
        return INTERACTIVE_POLICY

    policy = getattr(args, "command_policy", None)
    if isinstance(policy, CommandPolicy):
        return policy
    return CONSERVATIVE_COMMAND_POLICY


def policy_requires_repository(
    policy: CommandPolicy,
    args: argparse.Namespace,
) -> bool:
    """Return whether the selected invocation requires a Git repository."""
    if policy.repository is RepositoryPolicy.REQUIRED:
        return True
    if policy.repository is RepositoryPolicy.OPTIONAL:
        return False
    return getattr(args, "prompt_format", None) is None


def policy_uses_session_lock(
    policy: CommandPolicy,
    args: argparse.Namespace,
) -> bool:
    """Return whether the selected invocation uses the repository session lock."""
    if policy.locking is LockingPolicy.SESSION:
        return True
    if policy.locking is LockingPolicy.NONE:
        return False
    return getattr(args, "prompt_format", None) is None
