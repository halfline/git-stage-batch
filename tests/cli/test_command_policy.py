"""Tests for command-policy selection and invocation refinements."""

from __future__ import annotations

import argparse

from git_stage_batch.cli.argument_parser import parse_command_line
from git_stage_batch.cli.command_policy import (
    CONSERVATIVE_COMMAND_POLICY,
    INTERACTIVE_POLICY,
    LockingPolicy,
    RepositoryPolicy,
    SessionOwnershipPolicy,
    policy_for_args,
    policy_requires_repository,
    policy_uses_session_lock,
)


def _parse(*arguments: str) -> argparse.Namespace:
    args = parse_command_line(list(arguments), quiet=True)
    assert args is not None
    return args


def test_registered_alias_uses_canonical_policy():
    canonical = _parse("again")
    alias = _parse("a")

    assert alias.command_policy == canonical.command_policy


def test_unknown_synthetic_arguments_fail_closed():
    args = argparse.Namespace(command="future-command")

    policy = policy_for_args(args)

    assert policy is CONSERVATIVE_COMMAND_POLICY
    assert policy.session_ownership is SessionOwnershipPolicy.REQUIRE_AVAILABLE
    assert policy.locking is LockingPolicy.SESSION
    assert policy.repository is RepositoryPolicy.REQUIRED


def test_interactive_flag_overrides_implicit_show_policy():
    args = _parse()
    args.interactive_flag = True

    assert policy_for_args(args) is INTERACTIVE_POLICY


def test_status_prompt_refines_repository_and_lock_policy():
    args = _parse("status", "--for-prompt")
    policy = policy_for_args(args)

    assert policy.repository is RepositoryPolicy.OPTIONAL_FOR_PROMPT
    assert policy.locking is LockingPolicy.SESSION_EXCEPT_PROMPT
    assert policy_requires_repository(policy, args) is False
    assert policy_uses_session_lock(policy, args) is False


def test_regular_status_uses_repository_and_session_lock():
    args = _parse("status")
    policy = policy_for_args(args)

    assert policy_requires_repository(policy, args) is True
    assert policy_uses_session_lock(policy, args) is True


def test_history_verify_allows_foreign_owner_under_session_lock():
    args = _parse("rewrite", "verify")
    policy = policy_for_args(args)

    assert policy.session_ownership is SessionOwnershipPolicy.ALLOW_FOREIGN
    assert policy.locking is LockingPolicy.SESSION
    assert policy.repository is RepositoryPolicy.REQUIRED
    assert policy_uses_session_lock(policy, args) is True
