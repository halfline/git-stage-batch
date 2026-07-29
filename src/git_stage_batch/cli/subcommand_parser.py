"""Subcommand parser construction helpers."""

from __future__ import annotations

import argparse
from typing import Any, Protocol, cast

from .git_help import GitHelpArgumentParser


class Subparsers(Protocol):
    """Argument-parser operation needed by subcommand registrars."""

    def add_parser(
        self,
        name: str,
        **kwargs: Any,
    ) -> argparse.ArgumentParser:
        """Add and return one subcommand parser."""


def add_subcommand_parser(
    subparsers: Subparsers,
    command_name: str,
    **kwargs: Any,
) -> GitHelpArgumentParser:
    """Add a subcommand parser wired to its git help topic."""
    help_topic = kwargs.pop("help_topic", f"stage-batch-{command_name}")
    return cast(
        GitHelpArgumentParser,
        subparsers.add_parser(
            command_name,
            help_topic=help_topic,
            **kwargs,
        ),
    )
