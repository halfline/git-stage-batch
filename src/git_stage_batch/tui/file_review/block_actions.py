"""Block action command execution for file review."""

from __future__ import annotations


def block_review_file(file_path: str, *, local_only: bool) -> None:
    """Block a reviewed file from future review."""
    from ...commands.block_file import command_block_file

    command_block_file(file_path, local_only=local_only)


def unblock_review_file(file_path: str) -> None:
    """Remove a reviewed file from ignore state."""
    from ...commands.unblock_file import command_unblock_file

    command_unblock_file(file_path)
