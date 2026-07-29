"""Architecture rules for declarative CLI command policy."""

from __future__ import annotations

import argparse

from git_stage_batch.cli.command_policy import (
    CommandPolicy,
    LockingPolicy,
    PagerPolicy,
    RepositoryPolicy,
    SessionOwnershipPolicy,
    StateChangePolicy,
)
from git_stage_batch.cli.root_parser import build_root_parser


def _registered_subcommand_parsers(
) -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    root_parser = build_root_parser()
    subcommands = next(
        action
        for action in root_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    names_by_parser: dict[int, list[str]] = {}
    parsers_by_id: dict[int, argparse.ArgumentParser] = {}
    for name, parser in subcommands.choices.items():
        parser_id = id(parser)
        names_by_parser.setdefault(parser_id, []).append(name)
        parsers_by_id[parser_id] = parser
    return [
        (tuple(names_by_parser[parser_id]), parsers_by_id[parser_id])
        for parser_id in names_by_parser
    ]


def test_every_registered_command_declares_complete_policy():
    """Every parser registration must carry all runtime policy dimensions."""
    for names, parser in _registered_subcommand_parsers():
        assert "command_policy" in parser._defaults, names
        policy = parser._defaults["command_policy"]
        assert isinstance(policy, CommandPolicy), names
        assert isinstance(policy.session_ownership, SessionOwnershipPolicy), names
        assert isinstance(policy.locking, LockingPolicy), names
        assert isinstance(policy.repository, RepositoryPolicy), names
        assert isinstance(policy.pager, PagerPolicy), names
        assert isinstance(policy.state_changes, StateChangePolicy), names


def test_show_declares_scratch_changes_and_foreign_owner_access():
    """Show may cache selections without claiming literal read-only behavior."""
    parser_by_name = {
        name: parser
        for names, parser in _registered_subcommand_parsers()
        for name in names
    }

    policy = parser_by_name["show"]._defaults["command_policy"]

    assert policy.state_changes is StateChangePolicy.SCRATCH
    assert policy.session_ownership is SessionOwnershipPolicy.ALLOW_FOREIGN


def test_state_changes_do_not_imply_session_ownership_policy():
    """Journal purge is durable but independent of active session ownership."""
    parser_by_name = {
        name: parser
        for names, parser in _registered_subcommand_parsers()
        for name in names
    }

    policy = parser_by_name["journal"]._defaults["command_policy"]

    assert policy.state_changes is StateChangePolicy.DURABLE
    assert policy.session_ownership is SessionOwnershipPolicy.ALLOW_FOREIGN
