"""Block action command execution for file review."""

from __future__ import annotations

import sys

from ...exceptions import CommandError
from ...i18n import _
from .session import FileReviewSessionState
from ..prompts import confirm_destructive_operation


def apply_block_action(state: FileReviewSessionState, action: str) -> None:
    """Run a block or unblock action for the reviewed file."""
    from .file_browser import prompt_block_local_only

    if action == "B":
        if not confirm_destructive_operation(
            "block",
            _("This will add the reviewed file to ignore state."),
        ):
            return

        local_only = prompt_block_local_only()
        if local_only is None:
            return

        try:
            block_review_file(state.file_path, local_only=local_only)
        except CommandError as e:
            print(e.message, file=sys.stderr)
        return

    try:
        unblock_review_file(state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def block_review_file(file_path: str, *, local_only: bool) -> None:
    """Block a reviewed file from future review."""
    from ...commands.block_file import command_block_file

    command_block_file(file_path, local_only=local_only)


def unblock_review_file(file_path: str) -> None:
    """Remove a reviewed file from ignore state."""
    from ...commands.unblock_file import command_unblock_file

    command_unblock_file(file_path)
