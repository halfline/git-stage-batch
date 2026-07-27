"""Block action command execution for file review."""

from __future__ import annotations

import sys

from ...exceptions import CommandError
from ...i18n import _
from .session import FileReviewSessionState
from ..prompts import (
    confirm_destructive_operation,
    unlocked_input,
    wrap_prompt_for_readline,
)


def apply_block_action(state: FileReviewSessionState, action: str) -> None:
    """Run a block or unblock action for the reviewed file."""
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


def prompt_block_local_only() -> bool | None:
    """Prompt for the block-file destination."""
    try:
        choice = unlocked_input(
            wrap_prompt_for_readline(
                _("Block target [g]itignore, [l]ocal exclude, or q: ")
            )
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return None

    if choice in {"q", "quit", "cancel"}:
        return None
    if choice in {"l", "local", "local exclude"}:
        return True
    if choice in {"", "g", "gitignore"}:
        return False

    print(_("Invalid block target."), file=sys.stderr)
    return None


def block_review_file(file_path: str, *, local_only: bool) -> None:
    """Block a reviewed file from future review."""
    from ...commands.block_file import command_block_file

    command_block_file(file_path, local_only=local_only)


def unblock_review_file(file_path: str) -> None:
    """Remove a reviewed file from ignore state."""
    from ...commands.unblock_file import command_unblock_file

    command_unblock_file(file_path)
