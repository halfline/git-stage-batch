"""Subcommand parser construction helpers."""

from __future__ import annotations

from .git_help import GitHelpArgumentParser


def add_subcommand_parser(
    subparsers,
    command_name: str,
    **kwargs,
) -> GitHelpArgumentParser:
    """Add a subcommand parser wired to its git help topic."""
    help_topic = kwargs.pop("help_topic", f"stage-batch-{command_name}")
    return subparsers.add_parser(
        command_name,
        help_topic=help_topic,
        **kwargs,
    )
